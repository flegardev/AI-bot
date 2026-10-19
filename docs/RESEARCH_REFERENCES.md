# Bounded primary-source reference registry

Read on 2026-10-19. These are engineering references, not dependencies or evidence that our strategy has alpha.
No framework source has been copied wholesale. Scope: the six requested projects and their relevant documentation.

| Project / pinned revision | Inspected source | Adopted pattern | Limit |
|---|---|---|---|
| Freqtrade/FreqAI `3ce38b587d6c7a1ea479fb3e59d724dee5195561` | [rolling training](https://github.com/freqtrade/freqtrade/blob/3ce38b587d6c7a1ea479fb3e59d724dee5195561/docs/freqai-running.md), [leakage checks](https://www.freqtrade.io/en/stable/lookahead-analysis/) | Past-only rolling windows, reusable schema/model identifiers and prediction lineage | Crypto architecture and its default splits are not imported into our FX evaluation |
| NautilusTrader `42c3ca5f8f8d537f69772277e77792bb5b307ea5` | [backtest concepts](https://nautilustrader.io/docs/latest/concepts/backtesting/), [revision](https://github.com/nautechsystems/nautilus_trader/tree/42c3ca5f8f8d537f69772277e77792bb5b307ea5) | Separate market events, eligible fills and strategy decisions; declare execution assumptions | Our OHLC model cannot reconstruct tick paths, liquidity or queue position |
| Microsoft Qlib `54355232463878d2eebb91fe0ee5fa7fa1f5976c` | [recorder](https://github.com/microsoft/qlib/blob/54355232463878d2eebb91fe0ee5fa7fa1f5976c/qlib/workflow/recorder.py) | Record parameters, artifacts, source/data identities and failed experiments | Our lightweight local registry is not a distributed experiment server |
| Jesse `4fcbcdd500b9cd14ca9bdb142e4f46a34884a1ae` | [README and research capabilities](https://github.com/jesse-ai/jesse/blob/4fcbcdd500b9cd14ca9bdb142e4f46a34884a1ae/README.md) | Compare simple transparent strategies; report sensitivity and conditional uncertainty | Trade bootstrap is not a market generator or a guarantee; no optimization engine copied |
| QuantConnect LEAN `d4cac37514427c84d40064cecbf3f0e6be4b0137` | [fill model](https://github.com/QuantConnect/Lean/blob/d4cac37514427c84d40064cecbf3f0e6be4b0137/Common/Orders/Fills/FillModel.cs) | Make market price basis, execution timing and costs explicit and testable | No C# runtime or brokerage execution semantics are imported |
| TradingAgents `e8f5503e9b86d5c2710b0c76b1f686703968c9df` | [research graph](https://github.com/TauricResearch/TradingAgents/blob/e8f5503e9b86d5c2710b0c76b1f686703968c9df/tradingagents/graph/trading_graph.py) | Separate hypotheses, deterministic experiment execution and skeptical critique | An LLM narrative never supplies a price, fill, return or acceptance decision |

Attachments contained a research summary and a master task embedded in pasted conversation text; the two
longer attachment files had identical SHA256 `9c0531fc5d477163244e3ea70a7a965c662b8c6567212952a5a55f053768316e`.
The standalone named Markdown downloads were not present. The complete embedded master task and supplied
research summary were read; their historical claims about a larger search were not independently reasserted.
