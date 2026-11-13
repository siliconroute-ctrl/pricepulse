# Email alert when a store's scraper fails

What it does: after a real scrape run, if a store was blocked or found nothing, PricePulse emails
you one short message per store per day, with what to run next. Old prices stay in the app with
their date. Test runs (`--limit`, `--term`) and `--no-alert` never email.

## Set up on the scraper PC (once)

You need a mailbox to send from (admin@trolleyup.com) and its mail-server name (outgoing server smtp.trolleyup.com, port 465, SSL on, login required: these are the values xneelo showed). Ask
your host for "SMTP server, port and an app password". **Type the password only into your own
PowerShell window, never into a file, git or the chat.**

```powershell
setx PP_ALERT_SMTP_HOST "smtp.trolleyup.com"
setx PP_ALERT_SMTP_PORT "465"
setx PP_ALERT_SMTP_USER "admin@trolleyup.com"
setx PP_ALERT_SMTP_PASSWORD "type the password here"
setx PP_ALERT_TO "the address that should get the alerts"
```

Close PowerShell and open a new one (setx only affects new windows). Then test:

```powershell
cd C:\Users\joao\pricepulse\backend
.\.venv\Scripts\python -m pricepulse.alerts --test
```

"Test email sent." and a message in your inbox means it works. `setx` keeps the password in your
Windows account settings (not in the repo); remove it later with
`reg delete HKCU\Environment /v PP_ALERT_SMTP_PASSWORD /f`.
