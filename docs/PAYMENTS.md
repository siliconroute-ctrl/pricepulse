# Payments: the R99 monthly plan with PayFast

> **Not yet tested with PayFast.** The code follows PayFast's published rules and
> passes our own tests, but the build machine could not reach PayFast. Do the
> sandbox test below before taking real money.

## How it works

1. In the app, the person taps **Upgrade for R 99 a month**.
2. The server prepares a signed request; the phone goes to PayFast's own payment page.
   PricePulse never sees the card number.
3. PayFast tells our server about the first payment, and every monthly payment after
   that, at `https://YOUR-ADDRESS/api/v1/billing/payfast/itn`.
4. The server checks each notice four ways (signature, your merchant ID, the amount
   is exactly R99, and PayFast confirms it sent it) and then marks the account paid
   for one month, plus 3 days' grace for card retries.
5. If a month is not paid, the account drops back to the free plan by itself.
6. **Cancel my plan** in the app stops future payments; the person keeps Unlimited
   until the end of the month they paid for.

## Step 1: test in PayFast's sandbox (no real money)

1. Sign up at https://sandbox.payfast.co.za (free). It shows a test **Merchant ID**
   and **Merchant Key**. Under Settings, set a **passphrase** (any long phrase).
2. On the server, in `backend/.env`:
   ```
   PP_PUBLIC_URL=https://your-pricepulse-address
   PP_PAYFAST_MERCHANT_ID=<sandbox merchant id>
   PP_PAYFAST_MERCHANT_KEY=<sandbox merchant key>
   PP_PAYFAST_PASSPHRASE=<your passphrase>
   PP_PAYFAST_SANDBOX=1
   ```
   then `docker compose up -d`.
3. In the app: Account → **Upgrade**. Pay with the sandbox's test wallet.
4. Check, in this order:
   - PayFast does **not** show a "signature mismatch" error. If it does, the field
     order or passphrase is wrong: tell Claude Code the exact message.
   - Back in the app, Account shows **Unlimited plan** within a minute.
   - `docker compose logs api | grep "payment notice"` shows `"outcome": "paid"`.
     Other outcomes (`bad_signature`, `wrong_amount`, `not_confirmed`) say what failed.
5. Test **Cancel my plan** in the app, and confirm the subscription shows as
   cancelled in the PayFast sandbox dashboard. (Cancelling uses PayFast's API, which
   is the least-tested part. If it fails, the app says so; cancel in the dashboard.)

## Step 2: go live

1. Open a real PayFast merchant account (needs your ID, bank account and business
   details; allow a few days for verification). Ask PayFast to enable
   **recurring billing (subscriptions)**; it is not on by default.
2. Set a passphrase in the live account, then change `.env` to the live Merchant ID,
   Key and passphrase and set `PP_PAYFAST_SANDBOX=0`. Restart.
3. Make one real R99 payment yourself and check the same four things.
4. Cancel it and refund yourself from the PayFast dashboard.

## Looking after subscribers

| Task | How |
|---|---|
| How many people pay? | `python admin.py users` |
| Give someone Unlimited for free (tester, family) | `python admin.py plan <account-id> paid` |
| Take that away again | `python admin.py plan <account-id> free` |
| Refund | PayFast dashboard. Then, if they should lose access now, tell Claude Code |
| Someone paid but still shows Free | Check the logs for the outcome word (step 1.4) |

## Things to know

- **Fees.** PayFast takes roughly 3.2% + R2 per card payment (check their current
  pricing), so about R5 of each R99.
- **VAT.** If Silicon Route is VAT-registered, R99 includes VAT and you must issue
  tax invoices. Speak to your accountant.
- **Google Play.** If PricePulse goes on the Play Store, Google requires its own
  billing for subscriptions sold inside the app (and takes 15%). The web app can keep
  PayFast. Decide before Phase 6.
- **Records.** The server keeps the amount, date and PayFast reference of each
  payment. It does not keep names, emails or card details from PayFast.
