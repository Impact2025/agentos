import asyncio
from concurrent.futures import ThreadPoolExecutor
from .base import Tool, ToolResult

_executor = ThreadPoolExecutor(max_workers=2)


class MarketDataTool(Tool):
    name = "get_market_data"
    description = (
        "Haal actuele marktdata op: koers, dagverandering, volume, 52-weeks range, marktkapitalisatie. "
        "Werkt voor aandelen (bijv. 'ASML.AS', 'AAPL', 'SHELL.AS'), ETFs (bijv. 'VWRL.AS'), "
        "indices (bijv. '^AEX', '^GSPC', '^IXIC', '^GDAXI'), crypto (bijv. 'BTC-EUR', 'ETH-USD') "
        "en valuta (bijv. 'EURUSD=X'). Gebruik Yahoo Finance ticker-symbolen."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Yahoo Finance ticker, bijv. 'ASML.AS', '^AEX', 'BTC-EUR', 'AAPL'.",
            },
            "period": {
                "type": "string",
                "description": (
                    "Optioneel: historisch rendement periode. "
                    "Opties: '1d','5d','1mo','3mo','6mo','1y','2y','5y'. Leeg = geen historie."
                ),
                "default": "",
            },
        },
        "required": ["symbol"],
    }

    async def run(self, symbol: str, period: str = "") -> ToolResult:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, self._fetch, symbol.strip(), period.strip())

    def _fetch(self, symbol: str, period: str) -> ToolResult:
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol)
            info = ticker.info or {}

            if not info or info.get("trailingPegRatio") is None and not info.get("regularMarketPrice") and not info.get("currentPrice"):
                # Try a quick history check to see if symbol exists
                hist_check = ticker.history(period="5d")
                if hist_check.empty:
                    return ToolResult(self.name, f"Geen data gevonden voor ticker '{symbol}'. Controleer het Yahoo Finance ticker-symbool.", error=True)

            lines = [f"## Marktdata: {symbol}"]

            name = info.get("longName") or info.get("shortName") or symbol
            if name != symbol:
                lines.append(f"**{name}**")

            currency = info.get("currency", "")
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")

            if price:
                lines.append(f"**Koers:** {price:,.4f} {currency}")
                if prev_close and prev_close != 0:
                    change = price - prev_close
                    pct = (change / prev_close) * 100
                    sign = "+" if change >= 0 else ""
                    trend = "▲" if change >= 0 else "▼"
                    lines.append(f"**Dag:** {trend} {sign}{change:,.4f} ({sign}{pct:.2f}%)")

            day_high = info.get("dayHigh") or info.get("regularMarketDayHigh")
            day_low = info.get("dayLow") or info.get("regularMarketDayLow")
            if day_high and day_low:
                lines.append(f"**Dag range:** {day_low:,.2f} – {day_high:,.2f} {currency}")

            week52_high = info.get("fiftyTwoWeekHigh")
            week52_low = info.get("fiftyTwoWeekLow")
            if week52_high and week52_low:
                lines.append(f"**52w range:** {week52_low:,.2f} – {week52_high:,.2f} {currency}")

            market_cap = info.get("marketCap")
            if market_cap:
                if market_cap >= 1e12:
                    cap_str = f"{market_cap/1e12:.2f}T"
                elif market_cap >= 1e9:
                    cap_str = f"{market_cap/1e9:.1f}B"
                else:
                    cap_str = f"{market_cap/1e6:.0f}M"
                lines.append(f"**Marktkapitalisatie:** {cap_str} {currency}")

            volume = info.get("volume") or info.get("regularMarketVolume")
            avg_volume = info.get("averageVolume")
            if volume:
                vol_str = f"{volume:,}"
                if avg_volume:
                    vol_str += f" (gem. {avg_volume:,})"
                lines.append(f"**Volume:** {vol_str}")

            pe = info.get("trailingPE")
            if pe:
                lines.append(f"**P/E (trailing):** {pe:.1f}")
            fwd_pe = info.get("forwardPE")
            if fwd_pe:
                lines.append(f"**P/E (forward):** {fwd_pe:.1f}")

            div_yield = info.get("dividendYield")
            if div_yield:
                lines.append(f"**Dividend yield:** {div_yield*100:.2f}%")

            analyst = info.get("recommendationKey", "")
            target = info.get("targetMeanPrice")
            if analyst:
                lines.append(f"**Analyst consensus:** {analyst.upper()}")
            if target and price:
                upside = ((target - price) / price) * 100
                sign = "+" if upside >= 0 else ""
                lines.append(f"**Koersdoel (gem.):** {target:,.2f} {currency} ({sign}{upside:.1f}% upside)")

            if period:
                hist = ticker.history(period=period)
                if not hist.empty:
                    start_price = hist["Close"].iloc[0]
                    end_price = hist["Close"].iloc[-1]
                    total_pct = ((end_price - start_price) / start_price) * 100
                    sign = "+" if total_pct >= 0 else ""
                    trend = "+" if total_pct >= 0 else "-"
                    lines.append(f"\n**Rendement {period}:** {sign}{total_pct:.2f}%")
                    lines.append(f"**Hoog {period}:** {hist['High'].max():,.2f} | **Laag {period}:** {hist['Low'].min():,.2f}")

            sector = info.get("sector")
            industry = info.get("industry")
            if sector:
                lines.append(f"\n**Sector:** {sector}" + (f" · {industry}" if industry else ""))

            return ToolResult(self.name, "\n".join(lines))

        except ImportError:
            return ToolResult(self.name, "yfinance niet geïnstalleerd. Voeg 'yfinance' toe aan requirements.txt.", error=True)
        except Exception as e:
            return ToolResult(self.name, f"Fout bij ophalen marktdata voor '{symbol}': {e}", error=True)
