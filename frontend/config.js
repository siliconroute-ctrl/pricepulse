/* PricePulse settings for this copy of the app.

   apiBase   Where the price server is. Empty = same address as this page.
   authMode  "firebase" = real sign-in (the same accounts as TrolleyUp).
             "dev"      = test sign-in for local testing only; the real server refuses it.
   firebase  TrolleyUp's public web settings (these are not secrets).
   supportEmail  Shown on help and legal screens. */
window.PRICEPULSE_CONFIG = {
  apiBase: "",
  authMode: "firebase",
  supportEmail: "admin@trolleyup.com",
  firebase: {
    apiKey: "AIzaSyDUG46k-OckP7XyJeZLZ8msbKf8k2LPf-Y",
    authDomain: "trolleyup.com",
    projectId: "trolleyup-eff50",
    appId: "1:822425894527:web:2832bcbb8e9cdb5aefc94a"
  }
};
