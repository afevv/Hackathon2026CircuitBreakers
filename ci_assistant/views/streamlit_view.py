"""Streamlit View. Renders state and sends user actions to ViewModels."""
from __future__ import annotations

from ci_assistant.viewmodels.chat_viewmodel import ChatViewModel
from ci_assistant.viewmodels.comparison_viewmodel import ComparisonViewModel


def render(cmp_vm: ComparisonViewModel, chat_vm: ChatViewModel) -> None:
    import streamlit as st  # lazy import so the rest of the package works without Streamlit

    st.set_page_config(page_title="Competitor Intelligence Assistant", layout="wide")
    st.title("Competitor Intelligence Assistant")

    tab_compare, tab_chat = st.tabs(["Compare products", "Ask a question"])

    with tab_compare:
        labels = {p.id: p.label for p in cmp_vm.targets}
        target_id = st.selectbox("Danfoss product", list(labels), format_func=labels.get)
        if target_id and (cmp_vm.target is None or cmp_vm.target.id != target_id):
            cmp_vm.select_target(target_id)

        if cmp_vm.candidates:
            st.subheader("Candidate matches")
            st.dataframe(cmp_vm.candidates_dataframe(), hide_index=True, use_container_width=True)
            cand_labels = {c.product.id: c.product.label for c in cmp_vm.candidates}
            cand_id = st.selectbox("Compare against", list(cand_labels), format_func=cand_labels.get)
            if cand_id:
                cmp_vm.select_candidate(cand_id)

        if cmp_vm.result:
            st.subheader(f"{cmp_vm.result.left.label} vs {cmp_vm.result.right.label}")
            st.metric("Match score", cmp_vm.result.match_score)
            st.dataframe(cmp_vm.comparison_dataframe(), hide_index=True, use_container_width=True)
            if cmp_vm.result.uncertainties:
                st.warning("Uncertainties:\n\n" + "\n".join(f"- {u}" for u in cmp_vm.result.uncertainties))
        if cmp_vm.error:
            st.error(cmp_vm.error)

    with tab_chat:
        for msg in chat_vm.messages:
            with st.chat_message(msg.role):
                st.write(msg.text)
                if msg.citations:
                    st.caption("Sources: " + "; ".join(c.short() for c in msg.citations))
        if question := st.chat_input("Ask about competitor products..."):
            chat_vm.ask(question)
            st.rerun()
