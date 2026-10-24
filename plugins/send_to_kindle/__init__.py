#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, calibre contributors

"""Content server endpoints for the Send to Kindle book-page action."""

import json
import os
import re
import tempfile
import threading
import time
import uuid
from html import escape

from calibre.customize import ContentServerPlugin
from calibre.srv.errors import BookNotFound, HTTPBadRequest, HTTPForbidden, HTTPNotFound
from calibre.srv.routes import endpoint
from calibre.srv.routes import json as json_endpoint
from calibre.srv.utils import get_db

MAX_ATTACHMENT_SIZE = 50 * 1024 * 1024
JOB_TTL = 10 * 60
MAX_ACTIVE_JOBS = 4
MAX_STORED_JOBS = 256
KINDLE_FORMATS = frozenset({'docx', 'epub', 'htm', 'html', 'pdf', 'rtf', 'txt'})
KINDLE_FORMAT_ORDER = ('epub', 'docx', 'rtf', 'txt', 'pdf', 'html', 'htm')

_jobs = {}
_jobs_lock = threading.RLock()
_KINDLE_PAGE = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Send to Kindle</title>
<style>
html { background: transparent; }
body {
    background: transparent;
    color: inherit;
    font: 16px system-ui, sans-serif;
    margin: 0 auto;
    max-width: 34rem;
    padding: 0 1rem;
}
label { display: block; margin: .5rem 0; }
select, input, button {
    background: transparent;
    border: 1px solid currentColor;
    border-radius: .25rem;
    box-sizing: border-box;
    color: inherit;
    font: inherit;
    padding: .5rem;
    width: 100%;
}
label[aria-disabled="true"] {
    opacity: .55;
}
input:disabled {
    cursor: not-allowed;
}
button { cursor: pointer; margin-top: .5rem; }
#status { margin: .5rem 0 0; min-height: 1.5em; }
</style>
</head>
<body>
<main>
<label>Recipient
<select id="recipient"></select>
</label>
<label id="manual-label">New Kindle email address
<input id="manual-recipient" type="email" autocomplete="email" placeholder="name@kindle.com" disabled>
</label>
<label>Book format
<select id="format">__FORMAT_OPTIONS__</select>
</label>
<button id="send" type="button">Send</button>
<p id="status" role="status" aria-live="polite"></p>
</main>
<script>
const bookId = __BOOK_ID__;
const libraryId = new URLSearchParams(location.search).get('library_id');
const apiBase = new URL('../', location.href);
const recipient = document.getElementById('recipient');
const manualLabel = document.getElementById('manual-label');
const manualRecipient = document.getElementById('manual-recipient');
const format = document.getElementById('format');
const sendButton = document.getElementById('send');
const status = document.getElementById('status');
const manualValue = '__new_recipient__';

function syncDialogTheme() {
    const frame = window.frameElement;
    if (!frame) {
        return;
    }
    for (let ancestor = frame.parentElement; ancestor && ancestor !== window.parent.document.body; ancestor = ancestor.parentElement) {
        const style = window.parent.getComputedStyle(ancestor);
        if (style.backgroundColor !== 'rgba(0, 0, 0, 0)' && style.backgroundColor !== 'transparent') {
            document.documentElement.style.backgroundColor = style.backgroundColor;
            document.documentElement.style.backgroundImage = style.backgroundImage;
            document.documentElement.style.colorScheme = style.colorScheme;
            document.body.style.color = style.color;
            return;
        }
    }
}

async function request(url, options) {
    const response = await fetch(url, options);
    const text = await response.text();
    let data;
    try {
        data = JSON.parse(text);
    } catch (_) {
        const message = response.ok ? 'The server returned an invalid response.' : `Request failed (${response.status} ${response.statusText}).`;
        throw new Error(message);
    }
    if (!response.ok) {
        throw new Error(data.message || text || 'The request failed.');
    }
    return data;
}

function setRecipientMode() {
    const manual = recipient.value === manualValue;
    manualLabel.setAttribute('aria-disabled', String(!manual));
    manualRecipient.disabled = !manual;
    manualRecipient.required = manual;
}

async function loadRecipients() {
    try {
        const data = await request(new URL('recipients', apiBase));
        for (const saved of data.recipients || []) {
            const label = saved.alias ? `${saved.alias} — ${saved.email}` : saved.email;
            recipient.add(new Option(label, saved.email));
        }
    } catch (error) {
        status.textContent = `Could not load saved addresses: ${error.message}`;
    }
    recipient.add(new Option('Enter a new address…', manualValue));
    if (recipient.options.length === 1) {
        recipient.value = manualValue;
        status.textContent = status.textContent || 'No saved Kindle addresses. Enter one manually.';
    } else {
        recipient.selectedIndex = 0;
    }
    setRecipientMode();
}

async function pollStatus(jobId) {
    try {
        const data = await request(new URL(`status/${encodeURIComponent(jobId)}`, apiBase));
        status.textContent = data.message || '';
        if (data.state === 'queued' || data.state === 'sending') {
            setTimeout(() => pollStatus(jobId), 1200);
        } else {
            sendButton.disabled = false;
        }
    } catch (error) {
        status.textContent = `Could not check delivery status: ${error.message}`;
        sendButton.disabled = false;
    }
}

async function sendBook() {
    const address = recipient.value === manualValue ? manualRecipient.value.trim() : recipient.value;
    if (!address || (recipient.value === manualValue && !manualRecipient.checkValidity())) {
        status.textContent = 'Enter a valid Kindle email address.';
        (recipient.value === manualValue ? manualRecipient : recipient).focus();
        return;
    }
    sendButton.disabled = true;
    status.textContent = 'Queuing book for email…';
    try {
        const data = await request(new URL(`book/${bookId}`, apiBase), {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({recipient: address, format: format.value, library_id: libraryId})
        });
        if (!data.job_id) {
            throw new Error('The server returned an invalid job ID.');
        }
        await pollStatus(data.job_id);
    } catch (error) {
        status.textContent = error.message || 'Could not queue the email.';
        sendButton.disabled = false;
    }
}

recipient.addEventListener('change', setRecipientMode);
sendButton.addEventListener('click', sendBook);
syncDialogTheme();
if (!format.options.length) {
    status.textContent = 'This book has no format supported by Kindle email.';
    sendButton.disabled = true;
}
loadRecipients();
</script>
</body>
</html>'''


def normalize_kindle_address(value):
    """Return a single, syntactically plausible Kindle delivery address."""
    if not isinstance(value, str) or any(c in value for c in '\r\n\x00'):
        raise ValueError('Enter one Kindle email address')
    value = value.strip()
    if '<' in value or '>' in value or ',' in value or ';' in value:
        raise ValueError('Enter one Kindle email address without a display name')
    local, sep, domain = value.rpartition('@')
    if not sep or not local or '@' in local or not domain or re.search(r'\s', value):
        raise ValueError('Enter a valid Kindle email address')
    if domain.lower() not in {'kindle.com', 'kindle.cn', 'free.kindle.com', 'free.kindle.cn'}:
        raise ValueError('The recipient must use an Amazon Kindle delivery domain')
    return f'{local}@{domain.lower()}'


def configured_kindle_recipients(accounts, aliases):
    recipients = []
    seen = set()
    for address in accounts:
        try:
            email = normalize_kindle_address(address)
        except ValueError:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        alias = aliases.get(address, '')
        recipients.append({'email': email, 'alias': alias.strip() if isinstance(alias, str) else ''})
    return sorted(
        recipients,
        key=lambda recipient: (recipient['alias'].casefold() or recipient['email'].casefold(), recipient['email'].casefold()),
    )


def add_kindle_recipient(accounts, recipient):
    """Add recipient to email preferences unless it is already configured."""
    accounts = dict(accounts or {})
    for address in accounts:
        try:
            if normalize_kindle_address(address).lower() == recipient.lower():
                return accounts, False
        except ValueError:
            continue
    # Keep the new recipient available to the normal Send by email action, but
    # do not make it its default or enable automatic delivery.
    accounts[recipient] = ['EPUB', False, False]
    return accounts, True


def save_kindle_recipient(recipient):
    from calibre.utils.smtp import config as smtp_config

    config = smtp_config()
    opts = config.parse()
    accounts, changed = add_kindle_recipient(opts.accounts, recipient)
    if changed:
        config.set('accounts', accounts)
        aliases = dict(opts.aliases or {})
        for address in tuple(aliases):
            try:
                if normalize_kindle_address(address).lower() == recipient.lower():
                    aliases.pop(address)
            except ValueError:
                continue
        if aliases != (opts.aliases or {}):
            config.set('aliases', aliases)


def sanitize_attachment_name(title, fmt):
    title = re.sub(r'[\\/\x00-\x1f]+', '_', str(title or 'Book')).strip(' .') or 'Book'
    return f'{title[:160]}.{fmt.lower()}'


def kindle_page_html(book_id, formats):
    options = ''.join(f'<option value="{escape(fmt, quote=True)}">{escape(fmt.upper())}</option>' for fmt in formats)
    values = {
        '__BOOK_ID__': str(book_id),
        '__FORMAT_OPTIONS__': options,
    }
    return re.sub(r'__BOOK_ID__|__FORMAT_OPTIONS__', lambda match: values[match.group()], _KINDLE_PAGE)


def html_endpoint(ctx, rd, route, output):
    rd.outheaders.set('Content-Type', 'text/html; charset=UTF-8', replace_all=True)
    return output.encode('utf-8')


def _purge_expired_jobs(now=None):
    now = time.monotonic() if now is None else now
    expired = [key for key, job in _jobs.items() if job['state'] in {'sent', 'failed'} and now - job['created'] > JOB_TTL]
    for key in expired:
        _jobs.pop(key, None)


def _deliver(job_id, path, recipient, filename):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(state='sending', message='Sending email…')
    try:
        from calibre.utils.smtp import compose_mail, extract_email_address, sendmail
        from calibre.utils.smtp import config as smtp_config
        from polyglot.binary import from_hex_unicode

        opts = smtp_config().parse()
        if not opts.from_:
            raise ValueError('Configure a From address in Preferences > Sharing > Email')
        sender = extract_email_address(opts.from_)
        if not sender:
            raise ValueError('Calibre SMTP preferences do not contain a valid From address')
        from calibre.utils.random_ua import random_english_text

        message = compose_mail(
            opts.from_,
            recipient,
            random_english_text(),
            subject=random_english_text(min_words_per_sentence=3, max_words_per_sentence=9, max_num_sentences=1).rstrip('.'),
            attachment=path,
            attachment_name=filename,
        )
        sendmail(
            message,
            sender,
            [recipient],
            relay=opts.relay_host,
            username=opts.relay_username,
            password=from_hex_unicode(opts.relay_password),
            encryption=opts.encryption,
            port=opts.relay_port,
        )
    except Exception:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is not None:
                # Do not expose SMTP errors or server configuration to the browser.
                job.update(state='failed', message='Email delivery failed. Check Calibre SMTP settings and the server log.')
    else:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is not None:
                job.update(state='sent', message='The book was sent to Amazon.')
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _start_send(ctx, rd, book_id):
    if not rd.username:
        raise HTTPForbidden('Sign in to the content server before sending books by email')
    try:
        data = json.load(rd.request_body_file)
        if not isinstance(data, dict):
            raise TypeError('request body must be an object')
        recipient = normalize_kindle_address(data['recipient'])
        requested_fmt = data['format']
        if not isinstance(requested_fmt, str):
            raise TypeError('format must be a string')
        fmt = requested_fmt.lower().lstrip('.')
        library_id = data.get('library_id')
        if fmt not in KINDLE_FORMATS:
            raise ValueError('This book format is not supported for Kindle email')
    except (KeyError, TypeError, ValueError) as err:
        raise HTTPBadRequest(f'Invalid send request: {err}')
    except Exception as err:
        raise HTTPBadRequest(f'Invalid send request: {err}')

    db = get_db(ctx, rd, library_id)
    if not ctx.has_id(rd, db, book_id):
        raise BookNotFound(book_id, db)
    with db.safe_read_lock:
        format_data = db.format_metadata(book_id, fmt.upper())
        if not format_data:
            raise HTTPBadRequest(f'The book does not have a {fmt.upper()} format')
        size = format_data.get('size')
        if size is not None and size > MAX_ATTACHMENT_SIZE:
            raise HTTPBadRequest('The selected book format is larger than the 50 MiB email limit')
        metadata = db.get_metadata(book_id, get_cover=False)
        filename = sanitize_attachment_name(metadata.title, fmt)
        fd, path = tempfile.mkstemp(prefix='calibre-kindle-', suffix='.' + fmt)
        try:
            with os.fdopen(fd, 'wb') as dest:
                db.copy_format_to(book_id, fmt.upper(), dest)
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
            raise

    save_kindle_recipient(recipient)
    with _jobs_lock:
        _purge_expired_jobs()
        if sum(job['state'] in {'queued', 'sending'} for job in _jobs.values()) >= MAX_ACTIVE_JOBS:
            os.remove(path)
            raise HTTPForbidden('The server is currently sending the maximum number of Kindle emails')
        if len(_jobs) >= MAX_STORED_JOBS:
            finished = [key for key, job in _jobs.items() if job['state'] in {'sent', 'failed'}]
            for key in finished[:len(_jobs) - MAX_STORED_JOBS + 1]:
                _jobs.pop(key, None)
            if len(_jobs) >= MAX_STORED_JOBS:
                os.remove(path)
                raise HTTPForbidden('The server has too many recent email jobs; try again later')
        job_id = uuid.uuid4().hex
        _jobs[job_id] = {
            'created': time.monotonic(),
            'username': rd.username,
            'state': 'queued',
            'message': 'Email queued for delivery…',
        }
        thread = threading.Thread(target=_deliver, args=(job_id, path, recipient, filename), name='CalibreKindleEmail', daemon=True)
        try:
            thread.start()
        except Exception:
            _jobs.pop(job_id, None)
            os.remove(path)
            raise
    return {'job_id': job_id}


class SendToKindlePlugin(ContentServerPlugin):
    name = 'Send to Kindle from Content Server'
    description = 'Send books to a Kindle email address from the content server book page'
    author = 'calibre community'
    version = (1, 0, 0)
    minimum_calibre_version = (9, 12, 0)

    def content_server_endpoints(self):
        @endpoint('/send-to-kindle/capabilities', auth_required=False, postprocess=json_endpoint)
        def capabilities(ctx, rd):
            from calibre.utils.smtp import config as smtp_config

            opts = smtp_config().parse()
            return {'available': True, 'configured': bool(opts.from_), 'max_attachment_size': MAX_ATTACHMENT_SIZE}

        @endpoint('/send-to-kindle/ui/{book_id}', types={'book_id': int}, postprocess=html_endpoint)
        def send_to_kindle_page(ctx, rd, book_id):
            library_id = rd.query.get('library_id')
            db = get_db(ctx, rd, library_id)
            if not ctx.has_id(rd, db, book_id):
                raise BookNotFound(book_id, db)
            with db.safe_read_lock:
                formats = {fmt.lower() for fmt in db.formats(book_id)}
            formats = [fmt for fmt in KINDLE_FORMAT_ORDER if fmt in formats]
            return kindle_page_html(book_id, formats)

        @endpoint('/send-to-kindle/recipients', postprocess=json_endpoint)
        def recipients(ctx, rd):
            if not rd.username:
                raise HTTPForbidden('Sign in to the content server to view saved email addresses')
            from calibre.utils.smtp import config as smtp_config

            opts = smtp_config().parse()
            return {'recipients': configured_kindle_recipients(opts.accounts or {}, opts.aliases or {})}

        @endpoint('/send-to-kindle/book/{book_id}', types={'book_id': int}, methods={'POST'}, ok_code=200, postprocess=json_endpoint)
        def send(ctx, rd, book_id):
            return _start_send(ctx, rd, book_id)

        @endpoint('/send-to-kindle/status/{job_id}', postprocess=json_endpoint)
        def status(ctx, rd, job_id):
            if not rd.username:
                raise HTTPForbidden('Sign in to the content server to check email status')
            with _jobs_lock:
                _purge_expired_jobs()
                job = _jobs.get(job_id)
                if job is None or job['username'] != rd.username:
                    raise HTTPNotFound(f'No email job with id: {job_id}')
                if job['state'] == 'queued':
                    job['state'] = 'sending'
                    job['message'] = 'Sending email…'
                return {'state': job['state'], 'message': job['message']}

        return capabilities, send_to_kindle_page, recipients, send, status
