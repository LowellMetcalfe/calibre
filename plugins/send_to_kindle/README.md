# Send to Kindle from Content Server

This optional multi-file content-server plugin adds a **Send to Kindle** action
to content-server book pages. The action is not shown unless the plugin is
installed and enabled.

## Install

The plugin requires calibre 9.12.0 or later. From this directory, run:

```sh
calibre-customize -b .
```

Restart the content server after installing or updating the plugin. The
recipient picker, delivery form, and status display are supplied by the plugin
and open in a dialog from the book page.

## Configure

1. Configure a From address and, normally, an SMTP relay in **Preferences →
   Sharing → Email**. The plugin uses these existing calibre settings.
2. Add that From address to Amazon's [Approved Personal Document Email
   List](https://www.amazon.com/mycd) so Amazon accepts mail from it.
3. Access the content server through a signed-in account. Sending and job
   status are unavailable to anonymous users, even when anonymous browsing is
   enabled.

Kindle delivery addresses configured in calibre's email preferences appear with
their aliases; users can also enter an address manually. The plugin sends an
existing EPUB, PDF, DOCX, RTF, TXT, HTM, or HTML format, up to 50 MiB, to an
Amazon `@kindle.com`, `@kindle.cn`, `@free.kindle.com`, or
`@free.kindle.cn` address.

The plugin keeps the attachment temporarily on the server while email is sent.
Job status is private to the authenticated user who initiated the send and is
discarded ten minutes after delivery completes.
