# Accounts, the age check and limits

## What the rules are

| Rule | Value | Change it in `.env` |
|---|---|---|
| Items looked up per session | 45 | `PP_SESSION_ITEMS` |
| Free sessions per day | 3 (resets at midnight, South African time) | `PP_FREE_SESSIONS` |
| Paid plan | R99 a month, unlimited sessions, still 45 items each | `PP_PRICE_CENTS` |

- A session starts with the first lookup and ends when the person taps **Clear session**.
- Taking an item off the list does **not** give the slot back. Otherwise one session
  could be used to look up everything.
- Prices are only shown for items a signed-in person has looked up. Searching shows
  names and how many stores have the item, never prices. All limits are enforced on
  the server, so they cannot be skipped by changing the app.

## Sign-in: the same accounts as TrolleyUp

PricePulse uses TrolleyUp's Firebase project (`trolleyup-eff50`): Google, or email and
password. PricePulse never reads or writes TrolleyUp's lists. It only checks who is
signed in.

**You must do this once, or sign-in will fail on the live address:**

1. Open https://console.firebase.google.com → project **trolleyup** → **Authentication**
   → **Settings** → **Authorized domains** → **Add domain**.
2. Type PricePulse's address without `https://` (for example `prices.siliconroute.co.za`).
3. If the project's web API key is restricted by website (Google Cloud console →
   APIs & Services → Credentials), add the PricePulse address there too.

Not yet tested on a live address: Google's sign-in window opens through
`trolleyup.com`, which should work from another address once step 1 is done. If Google
sign-in fails but email works, tell Claude Code the message shown.

Things to know:

- Someone who creates their account in PricePulse has no TrolleyUp @username yet.
  TrolleyUp should ask for one the first time they open it. **Not yet tried.**
- TrolleyUp's Privacy Policy should gain one line saying the same sign-in also works
  for PricePulse. That is a change in the TrolleyUp repo; ask when you want it.
- TrolleyUp guests (people who never made an account) cannot use PricePulse.

## The ID number and POPIA

What happens to an ID number:

1. Sent to the server over HTTPS. Checked: 13 digits, check digit, real date.
2. Age is worked out. Saved: **18 or older: yes/no**; for under-18s, the date they turn 18.
3. A fingerprint is made with the server's secret key (`PP_ID_SECRET`) and saved, so
   the same ID cannot be used for a second account.
4. The number is thrown away. It is not in the database and not in the logs.

Your duties, in plain terms. **Please have an attorney confirm these:**

- You are the "responsible party". The draft `privacy.html` and `terms.html` say what
  is collected and why. Have them reviewed before launch.
- **Register an Information Officer** with the Information Regulator (free, online)
  before you collect personal information.
- **Under-18s:** POPIA needs a parent or guardian's consent to process a child's
  information. The app asks under-18s to get permission, but does not verify it.
  The safer option is to refuse under-18s entirely; say if you want that instead.
- **`PP_ID_SECRET`** is what makes the fingerprints safe. Make it once
  (`python -c "import secrets; print(secrets.token_hex(32))"`), keep a copy off the
  server, never put it in GitHub. Without the secret, a stolen database cannot be
  turned back into ID numbers.
- If the server is ever broken into, POPIA requires you to tell the Regulator and the
  people affected.
- People can delete their own data: Account → **Delete my PricePulse data**.

## Admin commands

Run from `backend/` (with Docker: `docker compose exec api python admin.py ...`).

| Command | What it does |
|---|---|
| `python admin.py users` | Number of accounts, age checks, under-18s, subscribers |
| `python admin.py plan <account-id> paid` | Give the Unlimited plan by hand |
| `python admin.py plan <account-id> free` | Remove a hand-given plan |

The account ID is the person's Firebase user ID: Firebase console → Authentication →
Users → find their email → copy **User UID**.

## Sending a list to TrolleyUp

**Send to TrolleyUp** on the List tab saves a small file. In TrolleyUp, the list's
admin opens the Items tab → **Import items from a CSV file** → picks that file →
reviews → adds. Each item arrives with its quantity, the store to buy it at
(cheapest per item, or everything at the cheapest single store) and the PricePulse
price as a note. Checkers and Pick n Pay liquor go under TrolleyUp's "Checkers
LiquorShop" and "Pick n Pay Liquor". Nothing in TrolleyUp had to change.

Checked by running the file through TrolleyUp's own import code (`csv.js`) and its
item rules. Not yet tried by hand in the live TrolleyUp app.
