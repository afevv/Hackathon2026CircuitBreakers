# Competitor Intelligence Assistant: MVVM template (Python)

A skeleton for the Danfoss hackathon case. The architecture is in place and the
domain logic is deliberately simple, so you can swap in the real extraction, RAG
and matching work. All sample data in `data/sample_products.json` is **made-up
placeholder data**, not real products.

## MVVM layout

```
Model       ci_assistant/models/       Plain dataclasses. No I/O, no UI. (Product, SourceRef, ComparisonResult...)
Services    ci_assistant/services/     Data + logic behind Protocol interfaces (repository, retriever,
                                       extractor, matcher, comparison engine, LLM client)
ViewModel   ci_assistant/viewmodels/   UI state + commands. Observable. Knows nothing about Streamlit/CLI.
View        ci_assistant/views/        Thin renderers. Streamlit and CLI views share the same ViewModels.
Wiring      ci_assistant/container.py  Service composition root (dependency injection)
App         app.py                     Keeps Streamlit ViewModels in session state and passes them to the View.
```

Dependency rule: View -> ViewModel -> Services/Models. Never the other way round.
That is what lets you test every ViewModel without a UI and swap the UI later.
The Streamlit entry point wires the ViewModels; the Streamlit View only renders them
and forwards user actions.

## Run

```bash
pip install -r requirements.txt
python -m ci_assistant                    # CLI demo on sample data
python -m ci_assistant --target DANFOSS-DEMO-B
streamlit run app.py                      # Streamlit UI
pytest                                    # tests
```

On Windows, double-click `run_app.bat` to start the Streamlit UI in your browser.
On its first run, it creates a project-local `.venv` and installs the dependencies.

Set `CI_LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=...` (see `.env.example`) to use a real LLM.
The default `stub` provider needs no key. If your hackathon provides an approved
enterprise model, implement `LLMClient` for it in `services/llm_client.py`.

## Where to put your hackathon work

| Case requirement                         | File to extend                                   |
|------------------------------------------|--------------------------------------------------|
| Retrieve public/Danfoss sources (RAG)    | `services/retrieval_service.py`                  |
| Extract attributes from PDFs/websites    | `services/extraction_service.py`                 |
| Common attribute format / units          | `models/attribute_schema.py`, `models/units.py`  |
| Cross-reference / matching               | `services/matching_service.py`                   |
| Comparison, differences, uncertainties   | `services/comparison_service.py`                 |
| Natural-language Q&A with citations      | `viewmodels/chat_viewmodel.py`                   |
| Battlecard / shortlist output            | new ViewModel + View                             |
| System-level comparison (stretch)        | new model `System(components=[Product])` + engine|
| Commercial insight (stretch)             | add price/availability to the schema + a service |

## Known TODOs in the template

- Conflict detection (same attribute, different values across sources) is stubbed in `comparison_service.py`.
- `RegexAttributeExtractor` is a toy; replace or complement it with LLM-based structured extraction.
- `KeywordRetriever` is plain keyword scoring; replace with embeddings/vector search.
- The Streamlit view was written without being run in the build environment (Streamlit wasn't installed there), so expect to fix small things on first launch.
