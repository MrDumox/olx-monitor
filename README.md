# Monitor OLX

Sprawdza nowe ogłoszenia z wybranych wyszukiwań na OLX.pl i wysyła je na kanał Discord.

Parsowanie OLX pochodzi z projektu [mar0ls/olx-monitor](https://github.com/mar0ls/olx-monitor) (MIT), który na bieżąco nadąża za zmianami na OLX, m.in. omija blokadę WAF przez `curl_cffi`. Z oryginału usunięte zostały GUI, AI i iMessage, a dodane: powiadomienia na Discord, obserwowanie dowolnych linków z OLX, sortowanie od najnowszych, odcinanie ogłoszeń "z okolicy" i automatyczne uruchamianie przez GitHub Actions.

## Jak działa

- Każde uruchomienie pobiera 1–2 strony wyników (od najnowszych) dla każdego linku i porównuje je z listą już widzianych ogłoszeń w `data/seen.json`.
- Nowe ogłoszenia idą na Discord jako karta: klikalny tytuł, cena (z dopiskiem "do negocjacji"), metraż, lokalizacja, data i miniatura zdjęcia.
- **Nowy link** przy pierwszym sprawdzeniu tylko zapamiętuje obecne ogłoszenia i wysyła jedną wiadomość startową, żeby nie zasypać kanału starymi ofertami.
- Oferty z otodom.pl, które OLX pokazuje w swoich wynikach, też są wysyłane.

## Co obserwować

Lista `SEARCH_URLS` na górze [olx_scraper.py](olx_scraper.py). Ustaw filtry na olx.pl (kategoria, miasto, promień, cena, metraż...) i wklej adres strony z wynikami:

```python
SEARCH_URLS = [
    "https://www.olx.pl/nieruchomosci/hale-magazyny/wynajem/bialystok/?search%5Bdist%5D=5&...",
    "https://www.olx.pl/nieruchomosci/biura-lokale/bialystok/?search%5Bdist%5D=5&...",
]
```

- Sortowanie od najnowszych jest wymuszane automatycznie, a zbędne parametry (np. `min_id` z powiadomień OLX) są usuwane.
- Linki można dowolnie dodawać i usuwać, bez resetowania pamięci.
- Link bez `/wynajem/` (np. `/biura-lokale/bialystok/`) obejmuje też ogłoszenia **sprzedaży**.

## Webhook Discorda

1. Potrzebujesz serwera Discord, na którym masz uprawnienia admina. Możesz założyć własny, prywatny: **+** na liście serwerów → **Utwórz własny**.
2. Ustawienia kanału (zębatka przy nazwie) → **Integracje → Webhooki → Nowy webhook** → **Kopiuj adres URL webhooka**.
3. Żeby dostawać powiadomienia push na telefonie: w aplikacji Discord przytrzymaj kanał → **Powiadomienia → Wszystkie wiadomości**.

Adres webhooka traktuj jak hasło: każdy, kto go zna, może pisać na ten kanał. Nie wklejaj go do kodu.

## Uruchomienie lokalne (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# test bez Discorda – tylko wypisuje, nic nie zapisuje
.venv\Scripts\python olx_scraper.py --dry-run

$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
.venv\Scripts\python olx_scraper.py
```

## Działanie 24/7 za darmo: GitHub Actions

Workflow [.github/workflows/monitor.yml](.github/workflows/monitor.yml) uruchamia skrypt co kilka minut na serwerach GitHuba i zapisuje `data/seen.json` z powrotem w repo.

1. Załóż repo na github.com (publiczne albo prywatne, patrz niżej) i wypchnij do niego ten folder.
2. W repo: **Settings → Secrets and variables → Actions → New repository secret** i dodaj `DISCORD_WEBHOOK_URL`.
3. Zakładka **Actions → OLX monitor → Run workflow**. Po chwili dostaniesz wiadomość startową, a dalej workflow działa już sam.

| | Repo publiczne | Repo prywatne |
|---|---|---|
| Darmowe minuty Actions | bez limitu | 2000 / miesiąc |
| Sprawdzanie | co 10 min (domyślnie) | co 30 min – zmień `cron` w `monitor.yml` na `"*/30 * * * *"` |
| Kto widzi kod | wszyscy (webhook w Secrets pozostaje ukryty) | tylko Ty |

Każde uruchomienie liczy się jako co najmniej 1 minuta, więc co 10 min to ok. 4300 min/mies., a co 30 min ok. 1450 min/mies.

### Warto wiedzieć

- GitHub uruchamia zadania z harmonogramu z opóźnieniem, przy dużym obciążeniu nawet 10–20 minut.
- Gdy uruchomienie się nie powiedzie (np. OLX zmieni stronę albo webhook został usunięty), GitHub wyśle Ci maila, a w zakładce Actions zobaczysz szczegóły.
- W repo publicznym GitHub wyłącza harmonogram po 60 dniach bez aktywności. Commity bota z `data/seen.json` zwykle to podtrzymują, a gdyby workflow się wyłączył, włączysz go jednym kliknięciem w zakładce Actions.
- OLX może kiedyś zacząć blokować adresy serwerów GitHuba (błędy 403). Wtedy alternatywą jest darmowa maszyna w Oracle Cloud Free Tier albo własny komputer z Harmonogramem zadań Windows.

## Licencja

MIT, patrz [LICENSE](LICENSE). Oryginalny kod: © 2026 mar0ls.
