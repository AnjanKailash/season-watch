# Season Watch Online (free)

One app, one database, opened from her laptop **and** her Android phone. Everything she adds on
one device shows on the other. Prices and news update every morning by themselves.

**How it fits together (all free tiers):**
- **Supabase**: the online database + her login.
- **GitHub Pages**: hosts the app's web page.
- **GitHub Actions**: runs the daily price-and-news update (7:00 am India time, Tue–Sat).

Setup takes about 30–40 minutes, once. Screen labels on these sites change from time to time,
so if a button name differs slightly, look for the closest match.

---

## Step 1: Create the database (Supabase)
1. Go to **supabase.com** → Start your project → sign up (Google sign-in is fine).
2. **New project**. Name: `season-watch`. Set a database password (save it). Region: **Mumbai** (or nearest). Free plan.
3. Wait 1–2 minutes for it to finish setting up.
4. Left menu → **SQL Editor** → New query. Open `supabase/schema.sql` from this folder, copy everything, paste, click **Run**.
   You should see "Success".

## Step 2: Create her login
1. Left menu → **Authentication** → **Users** → **Add user** → **Create new user**.
2. Enter her email and a password. Tick **Auto confirm user** if shown.
3. Authentication → **Sign In / Providers** (or Settings) → turn **off "Allow new users to sign up"**,
   so nobody else can create an account on your app.

## Step 3: Copy two keys from Supabase
Left menu → **Project Settings** → **API** (or **API Keys** / the **Connect** button):
- **Project URL**, like `https://abcdxyz.supabase.co`
- **anon / publishable key**: public, goes in the app.
- **service_role / secret key**: **private**, goes only into GitHub Secrets (Step 5). Never paste it anywhere else.

## Step 4: Put the app on GitHub
1. Sign up at **github.com**.
2. Click **+** → **New repository**. Name: `season-watch`. Choose **Public** (free GitHub Pages needs public;
   the code is visible but her data is not, because the database requires her login). Create.
3. Click **uploading an existing file**. Drag in **everything inside this folder**: `refresh.py`, `README.md`,
   and the `docs`, `supabase` and `.github` folders. Commit.
   - The `.github` folder is hidden on some computers. On Windows: File Explorer → View → Show → Hidden items.
     If dragging it fails, create it on GitHub instead: **Add file → Create new file**, name it
     `.github/workflows/daily.yml`, and paste the contents of that file.
4. Open `docs/config.js` on GitHub → pencil (edit) icon → paste your **Project URL** and **anon key** → Commit.

## Step 5: Give the daily job its keys
Repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Add two:
- `SUPABASE_URL`: the Project URL
- `SUPABASE_SERVICE_KEY`: the service_role / secret key

## Step 6: Run the first update
Repository → **Actions** tab → enable workflows if asked → **Daily market refresh** → **Run workflow**.
It takes 2–4 minutes. A green tick means prices, cycles, momentum and news are loaded.
After this it runs by itself every morning. Use **Run workflow** any time you want a fresh update.

## Step 7: Switch on the website
Repository → **Settings** → **Pages** → Source: **Deploy from a branch** → Branch: **main**, folder: **/docs** → Save.
After a minute the address appears, like `https://yourname.github.io/season-watch/`.

## Step 8: Open it on her devices
- **Laptop:** open the address in Chrome or Edge, sign in, and bookmark it.
- **Android:** open the address in Chrome, sign in, then menu (⋮) → **Add to Home screen** / **Install app**.
  It gets its own icon and opens full-screen like an app. She stays signed in.

---

## Good to know
- **Sync:** both devices read and write the same database. Switching back to the app reloads the latest data.
- **Prices** are the previous US close, updated around 7:00 am India time.
- **Free tier notes:** Supabase may pause free projects that see no activity for a while; the daily job counts as
  activity. If it ever pauses, open the Supabase dashboard and click **Restore**. Free plans can change their limits.
- **If the update fails** (red cross in Actions): open the run to see the error. Most often `yfinance` needs a newer
  version, which installs automatically on the next run, or a secret was pasted with an extra space.
- **Changing cycles or momentum stocks:** edit the lists at the top of `refresh.py` on GitHub.
- **Backup:** Supabase → Table Editor → `buys` → Export to CSV.
- Cycle and momentum views are rules based on past patterns and recent prices: estimates, not predictions.
  Not financial advice.
