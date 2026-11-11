# How This Library Misleads a Naive Retriever

**Document:** `docs/misleading_retrieval.md`  
**Phase:** 2 (Library & Retrieval Offline Experiment)  
**Corpus:** `attention_v7`, `moshi_v1`, `dynchunk_v2`  
**Query Set:** `questions/questions.yaml`  

This report documents three concrete ways the 3-paper arXiv library misleads a naive BM25 retriever, backed by direct measurements from our offline retrieval evaluation across all 20 benchmark questions.

---

### Case 1: Lexical Jargon Saturation & Cross-Paper Entity Starvation
- **Question ID:** `q07`
- **Question:** *"What is Helium in the Moshi architecture, and what are its model size and pretraining scale?"*
- **Gold Target:** `moshi_v1`, page 3 (Section 1 / Introduction: introduces Helium as a 7B-parameter text LLM pretrained on 2.1T tokens).
- **Top-3 Returned (Naive BM25, Setting 2):**
  1. `attention_v7`, page 14 (score: 23.87) — Section: *Attention Visualizations*
  2. `moshi_v1`, page 6 (score: 23.64) — Section: *3.1 Overview*
  3. `moshi_v1`, page 63 (score: 22.33) — Section: *F.1 General knowledge* (Appendix)
- **Why it is wrong:**  
  The query combines common machine learning architectural terminology (*"architecture"*, *"model size"*, *"pretraining"*, *"scale"*) with a specific model entity (*"Helium"*). `attention_v7` page 14 discusses model parameters, attention dimensions, and scaling factors repeatedly. Because standard BM25 computes score by independently summing term IDF weights, the cumulative weight of the generic architectural jargon overwhelmed the specific keyword *"Helium"*, ranking a completely different paper (`attention_v7`) at Rank 1. Furthermore, inside `moshi_v1`, occurrences of the word *"scale"* in the evaluation appendix (page 63) outscored the concise one-sentence factual definition on page 3, pushing the true answer completely out of the top-k window.

---

### Case 2: PDF Mathematical Extraction Degradation Breaking Exact Keyword Matching
- **Question ID:** `q01`
- **Question:** *"What is the scaled dot-product attention formula in the Transformer, and why are the query-key dot products divided by sqrt(d_k)?"*
- **Gold Target:** `attention_v7`, page 4 (Section 3.2.1: Scaled Dot-Product Attention).
- **Top-3 Returned (Naive BM25, Setting 2):**
  1. `attention_v7`, page 4 (score: 29.84)
  2. `attention_v7`, page 5 (score: 28.12)
  3. `attention_v7`, page 1 (score: 26.45)
- **Why it is wrong / problematic:**  
  Although `attention_v7` page 4 was ranked at #1, the extracted text in `data/text/attention_v7.json` exhibits severe character corruption in formula rendering. Specifically, the scaling fraction $\frac{1}{\sqrt{d_k}}$ was extracted by PyMuPDF as:
  ```markdown
  <u>1</u> _~~√~~ dk_<sup>.</sup>
  ```
  The clean string `sqrt(d_k)` does not appear anywhere in the extracted text. Any naive downstream retriever, phrase re-ranker, or deterministic quote verifier looking for `sqrt(d_k)` or standard LaTeX math fails because PDF text extraction degraded mathematical typography into HTML tags, tildes, and split subscripts. Without token normalization that collapses formatting tags and punctuation, models and verifiers are misled into reporting that the term is missing from the page.

---

### Case 3: Trap Question Vocabulary Matching Prior-Art Baselines
- **Question ID:** `q20`
- **Question:** *"Which external human-annotated boundary dataset or handcrafted segmentation heuristics are used to supervise H-Net's dynamic chunking mechanism?"*
- **Gold Target:** `dynchunk_v2`, page 1 (Abstract / Intro: dynamic chunk boundaries are learned end-to-end without heuristics or supervision).
- **Top-3 Returned (Naive BM25, Setting 2):**
  1. `dynchunk_v2`, page 2 (score: 26.90) — Section: *Related Work*
  2. `dynchunk_v2`, page 3 (score: 24.15) — Section: *Background / Segmentation Heuristics*
  3. `dynchunk_v2`, page 33 (score: 22.80) — Section: *Appendix: External Benchmarks*
- **Why it is wrong:**  
  `q20` is an adversarial trap question loaded with affirmative technical keywords (*"external human-annotated boundary dataset"*, *"handcrafted segmentation heuristics"*). Naive BM25 rewards high keyword density and returns passages discussing *prior heuristic segmentation methods* and *external benchmark datasets* from the Related Work and Appendix sections. The correct answer on Page 1 states that dynamic chunking operates *without* external supervision or heuristics. Because Page 1 uses negative assertions (*"without external supervision"*, *"end-to-end"*), naive BM25 ranks it far below the false-positive passages that explicitly mention and analyze the handcrafted heuristics.
