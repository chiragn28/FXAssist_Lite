# agent

LangGraph RAG agent: retrieve from Qdrant, grade relevance, generate an answer with citations, or fall back to "not enough information in the documents". Includes the citation validator, step and time caps, and injection-safe prompting. LangChain is used only for loaders and text splitters (ADR-007).

Built in **Phase 1**. Edge cases: DAT-*, RET-*, SAF-01 to SAF-05.
