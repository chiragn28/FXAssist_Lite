# Trading terms

The forex and CFD vocabulary behind FXAssist Lite's corpus, for anyone working on software at a broker. Each term has a plain definition and, where it matters, why an engineer at a broker cares. Regulatory figures are the EU and Australian retail rules as described in the corpus documents (ESMA, ASIC); check the current rules before quoting them.

## Instruments and markets

- **Forex (FX)**: the market for exchanging one currency for another. It trades over the counter, 24 hours a day from Monday morning in Sydney to Friday evening in New York.
- **Currency pair**: two currencies quoted against each other, written BASE/QUOTE. EUR/USD 1.0850 means one euro costs 1.0850 US dollars.
- **Base and quote currency**: the first currency is the one you buy or sell; the second is the one the price is expressed in. Profit and loss arrive in the quote currency and are converted to the account currency.
- **Majors, minors, exotics**: majors pair the US dollar with EUR, JPY, GBP, CHF, AUD, CAD or NZD and have the tightest spreads; minors (crosses) pair two non-USD majors, such as EUR/GBP; exotics include an emerging-market currency, such as USD/TRY, with wider spreads and larger gaps.
- **CFD (contract for difference)**: a contract to exchange the change in an asset's price between opening and closing, without owning the asset. Brokers offer CFDs on forex, indices, commodities, shares, bonds and crypto. Leveraged, so losses can exceed what a beginner expects; most of the corpus is about restricting how CFDs are sold to retail clients.
- **Spot**: a trade for immediate delivery (FX settles two business days later, T+2). A retail "spot FX" position at a broker is rolled forward each night instead of settling.
- **OTC (over the counter)**: traded directly between two parties, not on an exchange. A retail CFD's counterparty is the broker, which is why conflicts of interest and execution rules matter.
- **Index, commodity, share CFDs**: CFDs on the US 500, gold (XAU/USD), crude oil, or a single company's shares. Each has its own trading hours, contract size and margin rules.
- **Crypto CFDs**: CFDs on Bitcoin and other crypto-assets: very volatile, the lowest leverage caps (2:1 under ESMA and ASIC), and some regulators ban them for retail clients.

## Prices and trading costs

- **Bid and ask**: bid is the price you can sell at, ask (offer) the price you can buy at. A buy opens at the ask and closes at the bid.
- **Spread**: ask minus bid, the basic cost of a trade. It widens when liquidity is thin: at the daily rollover, over weekends, and around major news.
- **Pip**: the standard unit of price movement, the fourth decimal place for most pairs (0.0001) and the second for yen pairs (0.01).
- **Pipette (fractional pip)**: a tenth of a pip, the fifth decimal (or third for yen pairs). Most platforms quote in pipettes.
- **Point**: the smallest price increment a platform shows. On five-digit quotes a point is a pipette, which confuses beginners ("10 points = 1 pip").
- **Lot**: the trade-size unit. A standard lot is 100,000 units of the base currency, a mini lot 10,000, a micro lot 1,000. Volume is quoted in lots (1.00, 0.10, 0.01).
- **Pip value**: what one pip is worth for a position. For a standard lot of a USD-quoted pair it is about 10 USD.
- **Raw spread vs standard account**: a raw (or "ECN-style") account passes on the interbank spread, often near zero on EUR/USD, and charges a separate commission per lot; a standard account builds the broker's charge into a wider spread and charges no commission. A support assistant must not mix up the two cost models.
- **Commission**: a fee per lot, usually quoted "per side" (each of opening and closing) or "round turn" (both together). Getting per side vs round turn wrong doubles or halves a quoted cost.
- **Swap (rollover, overnight financing)**: interest credited or charged for holding a position past the daily cutoff, based on the interest-rate difference between the two currencies plus a broker markup. Usually charged three times on one weekday to cover the weekend. Some brokers offer swap-free (Islamic) accounts.
- **Slippage**: the difference between the requested price and the fill price, positive or negative. Common in fast markets and on stop orders.
- **Requote**: the dealer refuses the requested price and offers a new one; typical of dealing-desk execution, rare on market execution.
- **Liquidity**: how much can be traded near the current price without moving it. Brokers source it from liquidity providers (banks and non-bank market makers).
- **Market depth (order book, DOM)**: the volumes available at each price level. cTrader and MT5 show it; MT4 does not.

## Leverage and margin

- **Leverage**: trading a position larger than the money deposited. 30:1 means 1,000 USD of margin controls a 30,000 USD position, so a 1% move is a 30% gain or loss on that margin.
- **Retail leverage caps**: ESMA and ASIC restrict retail clients to 30:1 on major currency pairs, 20:1 on non-major pairs, gold and major indices, 10:1 on other commodities and non-major indices, 5:1 on individual shares, and 2:1 on crypto-assets. Japan caps retail forex at 25:1. Offshore entities may offer far more, which is a frequent client question and a compliance risk for any assistant.
- **Margin (initial or required margin)**: the money set aside to open a position: position value divided by leverage.
- **Balance, equity, used margin, free margin**: balance is deposits plus closed profit and loss; equity is balance plus open profit and loss; used margin is held by open positions; free margin is equity minus used margin, available for new trades.
- **Margin level**: equity divided by used margin, as a percentage. The platform's key risk number.
- **Margin call**: a warning when margin level falls to a set level (for example 100%): no new positions until it recovers.
- **Stop out (margin close-out)**: the broker closes positions automatically, largest loss first, when margin level falls to the stop-out level. ESMA and ASIC require close-out at 50% of initial margin for retail clients.
- **Negative balance protection**: a retail client cannot lose more than the money in the account; if a gap pushes equity below zero, the broker absorbs it. Required for retail clients under ESMA and ASIC rules.
- **Retail vs professional (wholesale) client**: professionals can waive retail protections, including the leverage caps, if they pass size, frequency and experience tests. Marketing that pushes clients to "opt up" is closely watched by regulators.

## Orders and execution

- **Market order**: buy or sell now at the best available price.
- **Pending orders**: a limit order trades at a better price than now (buy limit below the market, sell limit above); a stop order trades once price moves past a level (buy stop above, sell stop below), used for breakouts and to limit losses.
- **Stop loss and take profit**: orders attached to a position that close it at a chosen loss or profit level. A stop loss is not guaranteed: in a gap it fills at the next available price unless it is a guaranteed stop.
- **Trailing stop**: a stop loss that follows price by a set distance as the trade moves into profit.
- **Gap**: price jumps between two levels with no trading in between, typically at the weekend open or after major news. Gaps are why stops slip and why negative balance protection matters.
- **Dealing desk (market maker, B-book)**: the broker takes the other side of client trades and keeps client losses as revenue, managing its net risk internally.
- **No dealing desk: STP and ECN (A-book)**: orders pass straight through to liquidity providers; the broker earns from commission or a spread markup, not from client losses. Many brokers run a hybrid and route flow by client profile.
- **Liquidity provider (LP), prime broker, prime of prime**: the banks and firms that quote prices and take the broker's flow; smaller brokers reach top-tier banks through a "prime of prime".
- **Bridge and aggregator**: software that connects a trading platform to LPs and combines their quotes into the best bid and ask.
- **Last look**: an LP's right to reject a trade within milliseconds after seeing it; a source of rejections and slippage.
- **Latency and colocation**: time from order to fill. Brokers put trading servers in financial data centres (Equinix NY4 in New York, LD4/LD5 in London) close to their LPs; algorithmic clients rent a VPS nearby.
- **Best execution**: the regulatory duty to get clients the best possible result on price, cost, speed and likelihood of execution, and to be able to show it.

## Platforms and client tools

- **MetaTrader 4 and 5 (MT4, MT5)**: the most widely used retail trading platforms, by MetaQuotes. MT4 is mostly forex; MT5 adds more asset classes, depth of market and a different order model. Brokers run MT servers and connect them to LPs through a bridge.
- **Expert Advisor (EA)**: an automated strategy running inside MetaTrader, written in MQL4 or MQL5.
- **cTrader**: a platform by Spotware, built around ECN-style execution, with depth of market and cBots (automated strategies in C#).
- **FIX API**: a standard protocol for institutional and algorithmic clients to send orders directly, without a retail platform.
- **Copy trading, social trading, MAM/PAMM**: following another trader's positions automatically, or one manager trading many accounts. Heavily regulated, because followers inherit the leader's risk.
- **Web and mobile trading**: browser and app versions of the platforms; most retail traffic is mobile.

## Clients, compliance and risk

- **KYC (know your customer)**: verifying identity and address at onboarding; documents are personal data and must never leak into logs or prompts.
- **AML (anti-money laundering)**: monitoring deposits and withdrawals for laundering, with source-of-funds checks.
- **Appropriateness test**: checking that a retail client understands leveraged products before letting them trade; a requirement under EU rules.
- **Segregated client funds**: client money held in separate bank accounts from the broker's own money, so it is protected if the broker fails.
- **Risk warning**: the required statement of how many retail accounts lose money, such as "74% of retail investor accounts lose money when trading CFDs with this provider". Each provider must publish and update its own figure.
- **Product intervention**: a regulator restricting how a product is sold, as ESMA did for CFDs in 2018 and ASIC in 2021. The leverage caps, margin close-out, negative balance protection and the ban on bonuses and incentives come from these.
- **Inducements**: bonuses, gifts or trading credits used to attract clients; banned for retail CFD clients in the EU and Australia.
- **Investment advice**: a personal recommendation to buy or sell. Brokers that only execute orders must not give it, which is why FXAssist refuses advice by code and every answer carries a disclaimer.
- **Regulators**: ASIC (Australia), CySEC (Cyprus, under EU rules from ESMA), FCA (UK), FSA (Seychelles), CFTC and NFA (US retail forex). A broker usually runs one legal entity per regulator, and clients see different rules depending on which entity holds their account.

## Market context

- **Sessions**: Sydney, Tokyo, London and New York. Liquidity peaks when London and New York overlap; the daily rollover is usually at 17:00 New York time.
- **High-impact news**: scheduled releases such as US non-farm payrolls (NFP), CPI inflation and central-bank rate decisions. Spreads widen, slippage rises, and traffic to support and platforms spikes: a load and reliability event for every system at a broker.
- **Volatility**: how much and how fast price moves. Brokers raise margin requirements before events such as elections.
- **Fundamental vs technical analysis**: trading on economic data and news vs on price charts and indicators. Questions about either easily become requests for advice.
- **Hedging**: holding opposite positions to reduce risk. Clients hedge within an account; the broker hedges its net exposure with LPs.

## Broker business terms

- **Notional volume**: the total face value traded, often reported in billions of dollars per month. Brokers compete on it; it is not revenue.
- **Revenue per million (RPM)**: revenue per million dollars of notional volume, a common broker profitability measure.
- **Funded and active traders**: clients who deposited, and clients who traded in a period. Conversion from sign-up to first deposit is a core funnel metric.
- **Introducing broker (IB) and affiliate**: partners who refer clients for a share of revenue or a fee per client. Their marketing is the broker's regulatory responsibility.
- **White label**: a firm offering a trading platform under its own brand on another broker's infrastructure.
