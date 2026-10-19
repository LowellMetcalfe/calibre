#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, calibre contributors

import importlib.util
from pathlib import Path

from calibre.srv.tests.base import BaseTest


def load_plugin_module():
    repo_root = Path(__file__).resolve().parents[4]
    path = repo_root / 'plugins' / 'send_to_kindle' / '__init__.py'
    spec = importlib.util.spec_from_file_location('send_to_kindle_plugin_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSendToKindlePlugin(BaseTest):
    @classmethod
    def setUpClass(cls):
        cls.plugin_module = load_plugin_module()

    def test_kindle_addresses(self):
        normalize = self.plugin_module.normalize_kindle_address
        for address in ('reader@kindle.com', 'reader@KINDLE.CN', 'reader@free.kindle.com', 'reader@free.kindle.cn'):
            self.assertEqual(normalize(address), address.lower())
        for address in ('reader@example.com', 'reader@sub.kindle.com', 'reader@@kindle.com', ' reader @kindle.com', 'reader@kindle.com,other@kindle.com'):
            with self.assertRaises(ValueError, msg=address):
                normalize(address)
        with self.assertRaises(ValueError):
            normalize('reader@kindle.com\r\nBcc: attacker@example.com')

    def test_attachment_filename_is_safe(self):
        sanitize = self.plugin_module.sanitize_attachment_name
        self.assertEqual(sanitize('../Title\\Chapter', 'EPUB'), '_Title_Chapter.epub')
        self.assertEqual(sanitize('', 'pdf'), 'Book.pdf')

    def test_configured_kindle_recipients_include_aliases(self):
        recipients = self.plugin_module.configured_kindle_recipients(
            {'reader@kindle.com': [], 'reader@KINDLE.COM': [], 'other@example.com': [], 'backup@free.kindle.com': []},
            {'reader@kindle.com': 'My Kindle', 'backup@free.kindle.com': 'Backup'},
        )
        self.assertEqual(
            recipients,
            [
                {'email': 'backup@free.kindle.com', 'alias': 'Backup'},
                {'email': 'reader@kindle.com', 'alias': 'My Kindle'},
            ],
        )

    def test_plugin_page_escapes_title_and_renders_only_supported_formats(self):
        page = self.plugin_module.kindle_page_html(42, '<Book & title>', ['epub', 'pdf'])
        self.assertIn('&lt;Book &amp; title&gt;', page)
        self.assertIn('const bookId = 42;', page)
        self.assertIn('<option value="epub">EPUB</option>', page)
        self.assertIn('<option value="pdf">PDF</option>', page)
        placeholder_title = self.plugin_module.kindle_page_html(42, '__FORMAT_OPTIONS__', [])
        self.assertIn('<p>__FORMAT_OPTIONS__</p>', placeholder_title)
        self.assertNotIn('<p></p>', placeholder_title)

    def test_plugin_exposes_only_namespaced_routes(self):
        plugin = self.plugin_module.SendToKindlePlugin(None)
        endpoints = plugin.content_server_endpoints()
        self.assertEqual(
            {ep.route for ep in endpoints},
            {
                '/send-to-kindle/capabilities',
                '/send-to-kindle/ui/{book_id}',
                '/send-to-kindle/recipients',
                '/send-to-kindle/book/{book_id}',
                '/send-to-kindle/status/{job_id}',
            },
        )
        endpoints_by_route = {ep.route: ep for ep in endpoints}
        self.assertFalse(endpoints_by_route['/send-to-kindle/capabilities'].auth_required)
        self.assertTrue(endpoints_by_route['/send-to-kindle/ui/{book_id}'].auth_required)
        self.assertTrue(endpoints_by_route['/send-to-kindle/recipients'].auth_required)
        self.assertTrue(endpoints_by_route['/send-to-kindle/book/{book_id}'].auth_required)
        self.assertTrue(endpoints_by_route['/send-to-kindle/status/{job_id}'].auth_required)
        self.assertEqual(endpoints_by_route['/send-to-kindle/ui/{book_id}'].types, {'book_id': int})
        self.assertEqual(endpoints_by_route['/send-to-kindle/book/{book_id}'].methods, frozenset({'POST'}))
        self.assertEqual(endpoints_by_route['/send-to-kindle/book/{book_id}'].ok_code, 200)

    def test_static_capabilities_route_does_not_match_book_route(self):
        from calibre.srv.routes import Router

        plugin = self.plugin_module.SendToKindlePlugin(None)
        router = Router(plugin.content_server_endpoints())
        for path, expected_route in (
            ('/send-to-kindle/capabilities', '/send-to-kindle/capabilities'),
            ('/send-to-kindle/ui/42', '/send-to-kindle/ui/{book_id}'),
            ('/send-to-kindle/recipients', '/send-to-kindle/recipients'),
            ('/send-to-kindle/book/123', '/send-to-kindle/book/{book_id}'),
            ('/send-to-kindle/status/abc', '/send-to-kindle/status/{job_id}'),
        ):
            endpoint, _ = router.find_route(tuple(filter(None, path.split('/'))))
            self.assertEqual(endpoint.route, expected_route)
