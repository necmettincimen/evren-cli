"""
Built-in read-only remote command presets for `ssh_run`.

Long diagnostic one-liners used to live in the prompt for the model to copy.
Copies drifted (a reasoning turn "improved" the limit query and reported 0
containers without limits instead of 46) and every task paid for the text. The
model now passes `preset="k8s_health"` and EVREN builds the exact command.
"""

from __future__ import annotations

import re

_K8S_HEALTH = (
    'echo "== POD DURUM"; kubectl get pods {scope} --no-headers | awk \'{{print ${status}}}\' | sort | uniq -c; '
    'echo "== SORUNLU (Running/Completed disi veya hazir olmayan)"; kubectl get pods {scope} --no-headers | '
    'awk \'{{split(${ready},r,"/")}} (${status}!="Running" && ${status}!="Completed") || (${status}=="Running" && r[1]!=r[2])\'; '
    'echo "== RESTART>3"; kubectl get pods {scope} --no-headers | awk \'${restarts}+0>3\'; '
    'echo "== UYARI EVENT"; kubectl get events {scope} --field-selector type=Warning --sort-by=.lastTimestamp 2>/dev/null | tail -12 | cut -c1-220; '
    'echo "== DUGUM"; kubectl top nodes 2>&1; '
    'echo "boot: $(uptime -s 2>/dev/null) | k3s/kubelet: $(systemctl show k3s -p ActiveEnterTimestamp --value 2>/dev/null || systemctl show kubelet -p ActiveEnterTimestamp --value 2>/dev/null)"; '
    'echo "== BELLEK TOP"; kubectl top pods {scope} --sort-by=memory 2>&1 | head -8; '
    'echo "== LIMITSIZ CONTAINER"; kubectl get pods {scope} -o go-template=\'{{{{range .items}}}}{{{{range .spec.containers}}}}{{{{if not .resources.limits}}}}x{{{{"\\n"}}}}{{{{end}}}}{{{{end}}}}{{{{end}}}}\' | wc -l'
)

_K8S_RESOURCES = (
    'kubectl get pods {scope} -o go-template=\'{{{{range .items}}}}{{{{$n:=.metadata.namespace}}}}{{{{$p:=.metadata.name}}}}'
    '{{{{range .spec.containers}}}}{{{{$n}}}} {{{{$p}}}} {{{{.name}}}} '
    '{{{{with .resources.requests}}}}{{{{or .cpu "-"}}}} {{{{or .memory "-"}}}}{{{{else}}}}- -{{{{end}}}} '
    '{{{{with .resources.limits}}}}{{{{or .cpu "-"}}}} {{{{or .memory "-"}}}}{{{{else}}}}- -{{{{end}}}}{{{{"\\n"}}}}{{{{end}}}}{{{{end}}}}\' '
    '| (echo "NAMESPACE POD CONTAINER CPU_REQ MEM_REQ CPU_LIM MEM_LIM"; cat) | column -t'
)

# name -> (description shown to the model, template). Every preset is read-only.
PRESETS: dict[str, tuple[str, str]] = {
    "k8s_health": (
        "Küme/pod sağlık anlık görüntüsü: durum sayıları, hazır olmayan podlar, restart>3, uyarı event'leri, "
        "düğüm ve bellek kullanımı, boot/k3s başlangıç zamanı, limitsiz container sayısı",
        _K8S_HEALTH,
    ),
    "k8s_resources": (
        "Container başına CPU/bellek request ve limit tablosu (- = tanımsız)",
        _K8S_RESOURCES,
    ),
}

READ_ONLY_PRESETS = frozenset(PRESETS)

_NAMESPACE_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


def preset_names() -> list[str]:
    return sorted(PRESETS)


def build_preset(name: str, namespace: str | None = None) -> str:
    """Returns the shell command for a preset; ValueError for unknown input."""
    if name not in PRESETS:
        raise ValueError(f"Bilinmeyen preset: {name!r}. Geçerli: {', '.join(preset_names())}.")
    ns = (namespace or "").strip()
    if ns and not _NAMESPACE_RE.match(ns):
        raise ValueError(f"Geçersiz namespace: {ns!r}.")
    # `-n ns` output has no NAMESPACE column, so awk field numbers shift by one.
    scope, fields = (f"-n {ns}", {"ready": 2, "status": 3, "restarts": 4}) if ns else (
        "-A", {"ready": 3, "status": 4, "restarts": 5}
    )
    return _API_GUARD + PRESETS[name][1].format(scope=scope, **fields)


# With the API server down every section printed the same "connection refused"
# error (bursa3-k8s: 6 sections, ~40 noisy lines); stop early with the cause.
_API_GUARD = (
    'kubectl get --raw /readyz >/dev/null 2>&1 || { '
    'echo "== API SUNUCUSU YANIT VERMIYOR"; kubectl get --raw /readyz 2>&1 | tail -1 | cut -c1-200; '
    'echo "k3s: $(systemctl is-active k3s 2>/dev/null) | '
    'son baslangic: $(systemctl show k3s -p ActiveEnterTimestamp --value 2>/dev/null)"; '
    'journalctl -u k3s -n 6 --no-pager 2>/dev/null | cut -c1-200; exit 0; }; '
)
