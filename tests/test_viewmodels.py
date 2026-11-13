def test_comparison_vm_flow_and_notifications(container):
    vm = container.comparison_vm()
    changes: list[str] = []
    vm.subscribe(changes.append)

    vm.load()
    vm.select_target("DANFOSS-DEMO-A")
    vm.select_candidate(vm.candidates[0].product.id)

    assert vm.result is not None
    assert not vm.comparison_dataframe().empty
    assert {"targets", "target", "candidates", "result"} <= set(changes)


def test_comparison_vm_reports_unknown_product(container):
    vm = container.comparison_vm()
    vm.select_target("NOPE")
    assert vm.error and "Unknown" in vm.error


def test_chat_vm_cites_sources(container):
    vm = container.chat_vm()
    vm.ask("What is the max pressure of X?")
    answer = vm.messages[-1]
    assert answer.role == "assistant"
    assert answer.citations


def test_chat_vm_refuses_without_sources(container):
    vm = container.chat_vm()
    vm.ask("zzzzqqqq")
    assert "no matching sources" in vm.messages[-1].text
