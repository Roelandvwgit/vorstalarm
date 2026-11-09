# Vorstalarm installeren

Plak sleutels en codes alleen op de plekken die hieronder staan, niet in een chat.

## 1. Pushover-sleutels ophalen
1. Log in op [pushover.net](https://pushover.net).
2. Rechtsboven op de startpagina staat je **User Key**. Bewaar die even.
3. Kies onderaan **Create an Application/API Token**, noem de applicatie `Vorstalarm` en maak hem aan.
4. Bewaar de **API Token/Key** die je daarna ziet.

## 2. Lege opslag op GitHub maken
1. Ga naar [github.com/new](https://github.com/new).
2. Vul bij Repository name `vorstalarm` in en kies **Public**.
3. Laat README, .gitignore en license uit en kies **Create repository**.
4. Geef je GitHub-gebruikersnaam door aan Claude, dat de bestanden daarna plaatst.

De stappen hieronder doe je nadat de bestanden op GitHub staan.

## 3. Pushover-sleutels op GitHub zetten
In je repository: **Settings > Secrets and variables > Actions > New repository secret**. Maak er twee:

| Name | Secret |
|---|---|
| `PUSHOVER_USER` | je User Key |
| `PUSHOVER_TOKEN` | je API Token/Key |

## 4. Instelpagina aanzetten
1. In je repository: **Settings > Pages**.
2. Kies bij Branch `main` en map `/docs`, en dan **Save**.
3. Na een paar minuten staat de pagina op `https://GEBRUIKERSNAAM.github.io/vorstalarm/`.
4. Open die in Safari en kies **Deel > Zet op beginscherm**, op iPhone en iPad.

## 5. Toegangscode voor de instelpagina maken
1. Ga naar [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new).
2. Naam: `Vorstalarm instelpagina`. Kies een verloopdatum van een jaar.
3. Repository access: **Only select repositories**, kies `vorstalarm`.
4. Repository permissions: **Contents** op **Read and write**.
5. Maak de code aan en plak hem op de instelpagina onder **Toegangscode**. Doe dat op elk apparaat waarop je wilt kunnen wijzigen.

Als de code verloopt, blijft het alarm gewoon werken. Alleen wijzigen via de pagina lukt dan niet meer tot je een nieuwe code maakt.

## 6. Testen
1. In je repository: **Actions > Vorstcontrole > Run workflow**, laat het vinkje "Alleen een testmelding sturen" aan en start.
2. Binnen een minuut hoort op iPhone en iPad de melding "Vorstalarm test" binnen te komen.
3. Start hem nog een keer met het vinkje uit. Je krijgt dan "Vorstalarm actief" of, als er vorst verwacht wordt, "Vorst verwacht".
