# Data quality report

Generated 2026-10-11 17:50 UTC. Bars are stamped at their close time (UTC).
- tsx: yfinance (unofficial): split/dividend-adjusted daily .TO bars, stamped at the 16:00 Toronto close. Full history cached; checks cover last 5y; minimum usable history 5y.
- crypto: Alpaca crypto 1Day bars (they open at 00:00 UTC); historical crypto data needs no API keys. Full history cached; checks cover all history; minimum usable history 3y.
- USD/CAD (Bank of Canada FXUSDCAD): 2017-01-03 -> 2026-10-09, 2438 business days, longest gap 5 calendar days, latest 1.4271

## Per symbol

| asset_class | symbol | verdict | years_available | usable_years | longest_gap_days | first_bar | last_bar | window_start | bars_in_window | missing_days | unexpected_bars | zero_volume | nan_rows | ohlc_violations | big_moves | median_value_1y | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tsx | XIU.TO | OK | 27.01 | 27.01 | 5 | 1999-10-04 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 175181994 |  |
| tsx | XEG.TO | OK | 25.54 | 25.54 | 5 | 2001-03-23 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 50186076 |  |
| tsx | XFN.TO | OK | 25.53 | 25.53 | 5 | 2001-03-29 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 9549127 |  |
| tsx | RY.TO | OK | 31.74 | 31.74 | 5 | 1995-01-12 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 774855781 |  |
| tsx | TD.TO | OK | 31.74 | 31.74 | 5 | 1995-01-12 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 621137618 |  |
| tsx | BNS.TO | OK | 31.74 | 31.74 | 5 | 1995-01-12 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 387140036 |  |
| tsx | BMO.TO | OK | 31.74 | 31.74 | 5 | 1995-01-12 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 411877601 |  |
| tsx | ENB.TO | OK | 31.74 | 31.74 | 5 | 1995-01-12 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 503813711 |  |
| tsx | CNQ.TO | OK | 31.74 | 31.74 | 5 | 1995-01-12 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 745032693 |  |
| tsx | SU.TO | OK | 31.74 | 31.74 | 5 | 1995-01-12 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 453491520 |  |
| tsx | CNR.TO | OK | 29.88 | 29.88 | 5 | 1996-11-22 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 212348307 |  |
| tsx | SHOP.TO | OK | 11.39 | 11.39 | 5 | 2015-05-21 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 334229581 |  |
| tsx | ATD.TO | OK | 30.17 | 30.17 | 5 | 1996-08-06 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 99279435 |  |
| tsx | BCE.TO | OK | 30.43 | 30.43 | 5 | 1996-05-06 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 124534172 |  |
| tsx | TRP.TO | OK | 31.74 | 31.74 | 5 | 1995-01-12 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 341440570 |  |
| tsx | FNV.TO | OK | 18.85 | 18.85 | 5 | 2007-12-03 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 111062386 |  |
| tsx | ABX.TO | OK | 31.74 | 31.74 | 5 | 1995-01-12 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 276388183 |  |
| tsx | NTR.TO | OK | 8.76 | 8.76 | 5 | 2018-01-02 | 2026-10-09 | 2021-10-12 | 1255 | 0 | 0 | 0 | 0 | 0 | 0 | 150543746 |  |
| crypto | BTC/USD | OK | 5.77 | 5.77 | 1 | 2021-01-02 | 2026-10-11 | 2021-01-02 | 2109 | 0 | 0 | 0 | 0 | 0 | 0 | 142418 |  |
| crypto | ETH/USD | WARN | 5.77 | 5.77 | 1 | 2021-01-02 | 2026-10-11 | 2021-01-02 | 2109 | 0 | 0 | 0 | 0 | 0 | 3 | 58261 | big_moves |
| crypto | SOL/USD | DROP | 5.77 | 2.12 | 417 | 2021-01-02 | 2026-10-11 | 2021-01-02 | 1691 | 418 | 0 | 11 | 0 | 0 | 11 | 30210 | usable history 2.12y < 3y minimum |

## Dropped

- SOL/USD: usable history 2.12y < 3y minimum

## Details

- ETH/USD moves > 25%: 2021-01-04 +26.3%, 2021-05-20 -27.7%, 2021-05-25 +26.2%
- SOL/USD no bars from 2023-07-08 to 2024-08-26; usable history restarts 2024-08-27
- SOL/USD missing: 2023-06-25 to 2023-06-26 (2), 2023-07-08 to 2024-08-26 (416)
- SOL/USD zero volume: 2023-06-19, 2023-06-28, 2023-06-29, 2023-06-30, 2023-07-02, 2023-07-03, 2023-07-04, 2023-07-05, 2023-07-06, 2023-07-07 ... and 1 more
- SOL/USD moves > 25%: 2021-01-09 +37.1%, 2021-02-20 +28.3%, 2021-02-23 +30.4%, 2021-04-19 +30.0%, 2021-05-20 -37.4%, 2021-05-21 +26.5%, 2021-05-25 +27.9%, 2022-11-10 -42.3%, 2022-11-11 +25.3%, 2023-01-15 +32.8% ... and 1 more

## Cache activity this run

- XIU.TO: +0 bars
- XEG.TO: +0 bars
- XFN.TO: +0 bars
- RY.TO: +0 bars
- TD.TO: +0 bars
- BNS.TO: +0 bars
- BMO.TO: +0 bars
- ENB.TO: +0 bars
- CNQ.TO: +0 bars
- SU.TO: +0 bars
- CNR.TO: +0 bars
- SHOP.TO: +0 bars
- ATD.TO: +0 bars
- BCE.TO: +0 bars
- TRP.TO: +0 bars
- FNV.TO: +0 bars
- ABX.TO: +0 bars
- NTR.TO: +0 bars
- BTC/USD: +0 bars
- ETH/USD: +0 bars
- SOL/USD: +0 bars
