# Golden dataset changelog

## v5 (2026-11-16)
Added GT-101 to GT-106: long email threads from the hard tickets, labelled with the new thread rule. Added because pipeline v3 picks up thread noise that the golden set couldn’t see


## v4 (2026-11-14)
Found by comparing GPT and Opus ceiling runs.
- GT-069: added product "Google login" (Google login is a sign-in integration, consistent with GT-061 ('Google SSO').).
- GT-022: changed product to "Business" (product is the plan name 'Business', consistent with GT-006 ('Pro', not 'Pro seats').).



## v3 (2026-11-13)
Found by the first full eval run: these tickets failed with entity F1 0 because the labels
missed entities the labeling guide says should be there.
- GT-084: added date "Tuesday" (weekday names count as dates).
- GT-095: added product "Android" (the customer means the Android app).
- GT-100: added product "desktop" (apps count as products).

## v2 (2026-11-11)
- GT-010: summary now keeps the order ID BD-51230, as the labeling guide requires.

## v1 (2026-11-08)
- First version: 100 hand-written cases.