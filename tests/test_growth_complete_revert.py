"""真实训练中被否决的生长不能遗留输出参数，报告须对应还原后的网络。"""
import numpy as np

from lingshu.nn.hex_train import HexNet, train_infogap


def _trial(*, verify_steps=1, retain_gain=0.0, max_steps=1, max_growth=1):
    net = HexNet(n_kernels=2, n_mix=2, n_class=3, seed=7)
    rng = np.random.default_rng(9)
    x, y = rng.normal(size=(4, 3, 3, 1)), np.array([0, 1, 2, 0])
    snapshots = []
    original = net.grow_kernel

    def capture_then_grow():
        snapshots.append({"conv": net.conv.copy(), "mix": net.mix.copy(), "fc": net.fc.copy(),
            "K": net.K, "logits": net.forward(x)[0].copy(), "loss": net.loss(x, y)})
        original()

    net.grow_kernel = capture_then_grow  # instrumentation only; actual grow/gradient/loss run
    report = train_infogap(net, x, y, samples_per_step=10000, batch=4, max_steps=max_steps,
        stall_patience=1, max_growth=max_growth, verify_steps=verify_steps, retain_gain=retain_gain, seed=7)
    return net, x, y, snapshots, report


def test_revert_restores_the_output_parameters():
    net, _x, _y, snapshots, report = _trial()
    assert report["history"][-1]["verdict"] == "REVERT"
    assert np.array_equal(net.fc, snapshots[-1]["fc"]), "rejected growth left output parameter drift"
    np.testing.assert_array_equal(net.conv, snapshots[-1]["conv"])
    np.testing.assert_array_equal(net.mix, snapshots[-1]["mix"])
    assert net.K == snapshots[-1]["K"]


def test_revert_restores_the_predictions_at_the_growth_boundary():
    net, x, _y, snapshots, _report = _trial()
    assert np.array_equal(net.forward(x)[0], snapshots[-1]["logits"]), "rejected growth changed restored predictions"


def test_revert_reports_the_restored_loss_and_keeps_the_rejected_reading():
    net, x, y, snapshots, report = _trial()
    expected = round(snapshots[-1]["loss"], 5)
    assert report["final_D"] == expected, "REVERT reported the rejected candidate loss"
    assert report["history"][-1]["D"] == round(net.loss(x, y), 5)
    assert np.isfinite(report["history"][-1]["attempt_D"])


def test_accepted_growth_keeps_the_verification_updates():
    net, _x, _y, snapshots, report = _trial(retain_gain=2.0)
    assert report["history"][-1]["verdict"] == "GROW"
    assert net.K == snapshots[-1]["K"] + 1
    assert not np.array_equal(net.fc, snapshots[-1]["fc"])


def test_revert_without_verification_updates_preserves_the_whole_boundary():
    net, _x, _y, snapshots, report = _trial(verify_steps=0)
    assert report["history"][-1]["verdict"] == "REVERT"
    for name in ("conv", "mix", "fc"):
        np.testing.assert_array_equal(getattr(net, name), snapshots[-1][name])


def test_repeated_reverts_preserve_each_final_boundary_and_the_growth_budget():
    net, _x, _y, snapshots, report = _trial(max_steps=3, max_growth=3)
    assert len(snapshots) == report["reverts"] == 3
    assert report["growths"] == 0
    assert net.K == snapshots[-1]["K"] == 2
    assert np.array_equal(net.fc, snapshots[-1]["fc"]), "repeated reverts left output parameter drift"
