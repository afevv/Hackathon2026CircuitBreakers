from __future__ import annotations

from ci_assistant.container import Container


def run_cli(container: Container, target_id: str | None = None) -> None:
    """Non-interactive demo: best competitor match for one Danfoss product."""
    vm = container.comparison_vm()
    vm.load()
    if not vm.targets:
        print("No Danfoss products found.")
        return

    vm.select_target(target_id or vm.targets[0].id)
    if vm.error:
        print(vm.error)
        return

    print(f"Target: {vm.target.label}\n")
    print("Candidate matches:")
    print(vm.candidates_dataframe().to_string(index=False), "\n")

    if vm.candidates:
        vm.select_candidate(vm.candidates[0].product.id)
        print(f"Comparison vs {vm.result.right.label} (match score {vm.result.match_score}):")
        df = vm.comparison_dataframe().drop(columns=["Source (left)", "Source (right)"])
        df = df.rename(columns={"Δ %": "Delta %"})
        print(df.to_string(index=False))
        if vm.result.uncertainties:
            print("\nUncertainties:")
            for u in vm.result.uncertainties:
                print(f" - {u}")
