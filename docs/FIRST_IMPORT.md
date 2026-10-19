# Your first real import, step by step

This guide takes you from an empty PraktikumFinder to the first real Schülerpraktikum listings that students can see. You don't need to be a programmer; you copy a few commands and click through the admin area. It takes about 30–45 minutes, most of it reading and checking listings.

**What happens:** the app asks the official job search of the Bundesagentur für Arbeit (BA) for school internships in one city. The results land in a review list. Nothing is shown to students until you have checked it and clicked publish.

---

## 1. Before you start

You need:

* a computer with **Python 3.11 or newer** (Windows: install from python.org and tick "Add Python to PATH"; Mac: `brew install python` or python.org),
* the PraktikumFinder code: on GitHub click **Code → Download ZIP** and unzip it, or `git clone` it,
* an email address that the BA can contact you on if they have questions about your requests.

Read the **terms of use of the BA job search** on arbeitsagentur.de, plus the API description at <https://jobsuche.api.bund.dev/>. The interface is not an official open-data offer: the BA keeps the rights to the listings, and its terms restrict automated access and reuse. Only continue if your use is covered; if in doubt, ask the BA in writing before you run it regularly or publish listings.

**Turn off your browser's page translation** in the admin area. Otherwise the browser can translate the settings (for example `"wo"` into `"where"`), and the app can't read them.

## 2. Install and start the app

Open a terminal (Windows: "PowerShell"; Mac: "Terminal") **in the unzipped folder** and run:

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Mac / Linux
pip install -r requirements.txt
```

Then copy the settings file: copy `.env.example` to a new file called `.env`. Open `.env` in a text editor and change one line so the BA knows who is asking (use your own website and email):

```
SCRAPER_USER_AGENT=PraktikumFinderBot/0.1 (+https://your-site.example; contact: you@your-site.example)
```

Leave `SEED_DEMO_DATA=false`. That way only real listings appear.

Start the app:

```bash
uvicorn app.main:app
```

Leave this window open. Open <http://localhost:8000> in your browser. The first start takes a few seconds longer because it loads about 9,000 German postcodes.

## 3. Create your admin account

Open a **second** terminal in the same folder, activate the environment again (step 2, second line) and run:

```bash
python -m app.cli create-admin you@your-site.example
```

Choose a password with at least 12 characters. Then go to <http://localhost:8000/admin> and log in. The first time, you scan a QR code with an authenticator app on your phone (for example Aegis, FreeOTP, Google or Microsoft Authenticator) and type in the 6-digit code. You'll need the app every time you log in.

## 4. Run one source

1. In the admin area click **Quellen (Scraper)**.
2. Pick one city, for example **"BA Jobsuche – Schülerpraktikum Frankfurt am Main"**. You can change the city: in *Konfiguration* replace the value after `"wo":` with your city, and `"umkreis"` is the radius in km. Click **Speichern**.
3. Click **Jetzt ausführen**. The run happens in the background. It's polite on purpose (a pause of 2 seconds between requests), so it can take a few minutes.
4. Reload the page. Under *Letzter Lauf* you'll see a line like `OK: 18 gefunden, 18 neu, 0 aktualisiert, 0 unverändert.`

If the line starts with **Fehler**:

| Message | What it means | What to do |
|---|---|---|
| `antwortet mit 403` or `401` | The BA refused the request. | Make sure you have the latest version of the app (the BA retired its old interface in 2026, which also gave 403). If it keeps happening, the BA may have changed access again; check the API description. |
| `robots.txt verbietet …` | The site asks bots not to fetch this address. | The app respects that. Don't work around it. |
| `Netzwerkfehler` / `nicht erreichbar` | Your computer couldn't reach the site. | Check your internet connection or firewall. |

`0 gefunden` is not an error. It means the BA had no school internships for that city and radius right now. Try a bigger city or a larger `umkreis`.

## 5. Review and publish

1. Click **Prüfung**. The list **"Warten auf Prüfung"** shows the imported listings.
2. Open a listing. At the top, under **Hinweise**, you'll see what the app read automatically from the ad, for example *"Automatisch aus dem Anzeigentext erkannt – bitte prüfen: Alter ab 14 Jahre; Klasse 8–10; Termine 01.02.2027–12.02.2027"*. These are suggestions, not facts.
3. Click **Original** to open the ad itself. Check against it:
   * Is it really for **school students** (not university students or trainees)? If not, set the status to *Abgelehnt*.
   * **Age and grade**: correct, or empty if the ad doesn't say. Never guess.
   * **Dates**: practicum periods, or tick *Flexible Termine* if the employer is flexible.
   * **How to apply**: email or official link.
   * **Field (Bereich)**: the app suggests one; correct it if needed.
4. If the age, grade or date fields are empty but the ad text in the description mentions them, click **Alter, Klasse und Termine aus dem Text erkennen**. It only fills empty fields; check the result.
5. Click **Speichern** at the bottom of the form.
6. Under **Prüfung erfassen** choose a result and write the source, for example *"Anzeige auf arbeitsagentur.de geprüft am 18.10.2026"*. If you also called or emailed the employer, say so. That gives the listing a better trust label for students.
7. Under **Status ändern** choose **Veröffentlicht** and click **Übernehmen**. If something required is missing, the app tells you what.

Do this for each listing. It's normal to reject quite a few: the BA search also finds internships that aren't meant for school students.

**Tip:** under **Import & Export** click **Übersicht als Excel herunterladen** for a readable spreadsheet of all listings. It is sorted by employer and town, explains every column and flags what to check, for example ads whose expiry date has already passed.

## 6. Check what students see

Open <http://localhost:8000> in a private browser window, search with your city's postcode, an age and a grade, and open one of your listings. Read it as a 14-year-old would.

## 7. Keep it fresh

* **More cities:** repeat step 4 for the other prepared sources, or add a new source with your own city.
* **Automatic runs:** set `SCRAPE_INTERVAL_HOURS=24` in `.env` and restart the app. Then every day the app runs the sources you ticked as **aktiv**, expires outdated listings and checks links. New listings still wait for your review.
* **Dead links:** on the admin **Übersicht** click **Alle Links jetzt prüfen** (it also runs with the daily schedule). Listings whose page is gone (404) appear under **Prüfung → Link defekt**. A site that blocks automatic checks is shown as "nicht prüfbar", not as broken. Nothing is unpublished automatically; you decide.
* **Listings that disappeared from the BA** appear under **Prüfung → Nicht mehr in der Quelle gefunden**. Usually the place has been filled; close the listing.
* **Re-check** published listings regularly (default every 90 days). They show up under **Prüfung überfällig**.

## Demo listings

If you turned demo data on to try the app (`SEED_DEMO_DATA=true`), remove the 12 fictional listings before real students use the site: on the admin **Übersicht** click **Demo-Angebote entfernen**, or run `python -m app.cli remove-demo`. Then set `SEED_DEMO_DATA=false` so they don't come back on the next start.
