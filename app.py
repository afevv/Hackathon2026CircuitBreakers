"""Streamlit composition root: `streamlit run app.py`."""
import streamlit as st

from ci_assistant.config import Settings
from ci_assistant.container import build_container
from ci_assistant.views.streamlit_view import render


def main() -> None:
    if "container" not in st.session_state:
        container = build_container(Settings.from_env())
        comparison_vm = container.comparison_vm()
        comparison_vm.load()
        st.session_state.container = container
        st.session_state.cmp_vm = comparison_vm
        st.session_state.chat_vm = container.chat_vm()

    render(st.session_state.cmp_vm, st.session_state.chat_vm)


if __name__ == "__main__":
    main()
