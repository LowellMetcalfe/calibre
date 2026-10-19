# Send to Kindle from Content Server

This is a multi-file calibre content-server plugin. Install it from a ZIP
created from this directory with calibre's `calibre-customize -b` command.

The plugin requires calibre 9.12.0 or later. It adds authenticated routes used
by the Send to Kindle action in the calibre content-server book page. The
book-details page has a small integration hook; the recipient picker, delivery
form, and status display are provided by the plugin and shown in a dialog on
the book page.

Before sending, configure a From address and (recommended) SMTP relay in
Preferences → Sharing → Email. The plugin uses those existing calibre SMTP
preferences. Kindle delivery addresses configured there appear in the plugin
page with their aliases when set; you can also enter an address manually. It
sends only existing EPUB, PDF, DOCX, RTF, TXT, or HTML formats, and rejects
attachments larger than 50 MiB. The Kindle address must be an
Amazon `@kindle.com`, `@kindle.cn`, `@free.kindle.com`, or `@free.kindle.cn`
address. Sign-in is required, even if the content server otherwise permits
anonymous browsing.

The plugin keeps the attachment temporarily on the server while email is sent.
Job status is private to the authenticated user who initiated the send and is
discarded ten minutes after delivery completes.
