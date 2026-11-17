# @so10xai Instagram Auto-DM

कोई post/reel पर keyword (EDITOR, TOKENS, ULTRA, KIT) comment करे → उसे **अपने आप DM** जाता है + comment पर छोटा public reply ("Check your DMs 📩").
कोई सीधे DM में keyword भेजे → DM से जवाब।

Pure Python (कोई pip package नहीं), Wasmer Edge पर चलता है। Keywords और messages `src/rules.json` में बदलें।

---

## Step 1 — Meta App बनाओ (Instagram Login)

1. Instagram account **Professional (Creator/Business)** होना चाहिए।
2. https://developers.facebook.com/apps → **Create App** → use case **"Manage messaging & content on Instagram"** चुनो।
3. App Dashboard → **Instagram → API setup with Instagram login**:
   - **Generate access tokens** में @so10xai add करो → login → token copy करो (`IG_ACCESS_TOKEN`)।
   - वहीं account का **ID** दिखेगा (`IG_USER_ID`)।
   - **App settings → Basic** (Instagram section) में **Instagram app secret** (`IG_APP_SECRET`)।
4. Permissions चाहिए: `instagram_business_basic`, `instagram_business_manage_comments`, `instagram_business_manage_messages`।
5. Instagram app में: **Settings → Messages and content → Connected tools → "Allow access to messages"** ON करो।

## Step 2 — Wasmer पर deploy

```bash
cd auto-dm
cp .env.example .env        # values भरो; VERIFY_TOKEN कोई भी random शब्द
wasmer login
wasmer deploy               # wasmer.toml में "so10xai" को अपने Wasmer username से बदल लेना
wasmer app secrets create --from-file=.env
```

(या dashboard: App → Settings → Environment Vars → add → **Save and Redeploy**)

Check: `https://<आपका-app>.wasmer.app/health` खोलो → `"configured": true` दिखना चाहिए।

> अगर `wasmer deploy` config पर error दे: `wasmer app create` → **Python HTTP server** template चुनो, फिर उसकी `src/main.py` की जगह इस folder का `src/main.py` और `src/rules.json` डाल दो।

## Step 3 — Instagram से connect (Webhook)

App Dashboard → **Instagram → API setup → Configure webhooks**:
- **Callback URL:** `https://<आपका-app>.wasmer.app/webhook`
- **Verify token:** वही जो `.env` में `VERIFY_TOKEN` है → **Verify and save**
- Fields subscribe करो: **comments** और **messages**
- "Generate access tokens" वाले हिस्से में account के आगे **webhook subscription ON** करो।

## Step 4 — Live करो

- Development mode में webhook सिर्फ app के tester/role वाले accounts के लिए आता है — पहले अपने दूसरे account को **App roles → Instagram Tester** बनाकर test करो।
- सबके लिए चालू करने को: App को **Live** करो और ऊपर की permissions के लिए **App Review (Advanced Access)** submit करो।

## ध्यान रखें

- DM सिर्फ comment के **7 दिन के अंदर** और **एक comment पर एक बार** भेज सकते हैं (Instagram का नियम)।
- Token ~60 दिन चलता है — 50 दिन में dashboard से नया token बनाकर `IG_ACCESS_TOKEN` update करो।
- सिर्फ DM चाहिए, public reply नहीं → `PUBLIC_REPLY=0`।
- Local test: `python3 tests/test_local.py` (कुछ भी Instagram पर नहीं भेजता)।
