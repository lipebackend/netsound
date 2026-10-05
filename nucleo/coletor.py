"""Coleta PASSIVA e SOMENTE LEITURA de dados da rede local.

Fontes (nenhuma exige privilégios):
  * /proc/net/dev                      -> bytes/pacotes por interface
  * `ss -H` ou /proc/net/{tcp,tcp6,udp,udp6} -> conexões TCP/UDP ativas
  * /proc/net/arp e `ip neigh`         -> vizinhos (dispositivos) vistos na LAN

NÃO faz varredura, NÃO captura pacotes e NÃO lê conteúdo de tráfego.
"""
import ipaddress
import os
import shutil
import socket
import subprocess
import time

from .mapeamento import CLASSES, ORDEM_CLASSES, classificar

PREFIXOS_IGNORADOS = ("veth", "docker", "br-", "virbr", "vnet", "cni", "flannel")
ESTADOS_TCP_PROC = {"01": "ESTAB", "02": "SYN-SENT", "03": "SYN-RECV"}
ESTADOS_TCP_SS = {"ESTAB", "SYN-SENT", "SYN-RECV"}


# ----------------------------------------------------------------- interfaces
def ler_interfaces(caminho="/proc/net/dev"):
    """Retorna {iface: (rx_bytes, rx_pacotes, tx_bytes, tx_pacotes)}."""
    res = {}
    with open(caminho) as f:
        linhas = f.read().splitlines()[2:]
    for linha in linhas:
        if ":" not in linha:
            continue
        nome, resto = linha.split(":", 1)
        c = resto.split()
        if len(c) < 16:
            continue
        res[nome.strip()] = (int(c[0]), int(c[1]), int(c[8]), int(c[9]))
    return res


# ------------------------------------------------------------------ conexões
def _ip4(h):
    return socket.inet_ntoa(bytes.fromhex(h)[::-1])


def _ip6(h):
    b = bytes.fromhex(h)
    b = b"".join(b[i:i + 4][::-1] for i in range(0, 16, 4))
    ip = ipaddress.IPv6Address(b)
    return str(ip.ipv4_mapped) if ip.ipv4_mapped else str(ip)


def _ler_proc_arquivo(caminho, proto, v6):
    out = []
    try:
        with open(caminho) as f:
            linhas = f.read().splitlines()[1:]
    except OSError:
        return out
    conv = _ip6 if v6 else _ip4
    for linha in linhas:
        c = linha.split()
        if len(c) < 4:
            continue
        try:
            lh, lp = c[1].split(":")
            rh, rp = c[2].split(":")
            lip, rip = conv(lh), conv(rh)
            lport, rport = int(lp, 16), int(rp, 16)
        except ValueError:
            continue
        st = c[3]
        if proto == "tcp":
            if st not in ESTADOS_TCP_PROC:
                continue
        else:  # UDP: só soquetes "conectados" (com par remoto)
            if rport == 0:
                continue
        out.append((proto, lip, lport, rip, rport))
    return out


def conexoes_proc():
    r = []
    for arq, proto, v6 in (("tcp", "tcp", False), ("tcp6", "tcp", True),
                           ("udp", "udp", False), ("udp6", "udp", True)):
        r += _ler_proc_arquivo("/proc/net/" + arq, proto, v6)
    return r


def _sep_endereco(txt):
    ip, _, porta = txt.rpartition(":")
    ip = ip.strip("[]").split("%")[0]
    return ip, porta


def conexoes_ss():
    """Usa `ss -H -tuan` (somente leitura). Levanta OSError se indisponível."""
    p = subprocess.run(["ss", "-H", "-tuan"], capture_output=True, text=True, timeout=3)
    if p.returncode != 0:
        raise OSError("ss falhou: " + p.stderr.strip())
    out = []
    for linha in p.stdout.splitlines():
        c = linha.split()
        if len(c) < 6:
            continue
        proto, estado = c[0], c[1]
        if proto == "tcp" and estado not in ESTADOS_TCP_SS:
            continue
        if proto == "udp" and estado != "ESTAB":
            continue
        if proto not in ("tcp", "udp"):
            continue
        lip, lp = _sep_endereco(c[4])
        rip, rp = _sep_endereco(c[5])
        if not (lp.isdigit() and rp.isdigit()) or int(rp) == 0:
            continue
        if rip.startswith("::ffff:"):
            rip = rip[7:]
        if lip.startswith("::ffff:"):
            lip = lip[7:]
        out.append((proto, lip, int(lp), rip, int(rp)))
    return out


def _e_loopback(ip):
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


# ------------------------------------------------------------------ vizinhos
def vizinhos_arp(caminho="/proc/net/arp"):
    res = {}
    try:
        with open(caminho) as f:
            linhas = f.read().splitlines()[1:]
    except OSError:
        return res
    for linha in linhas:
        c = linha.split()
        if len(c) < 6:
            continue
        ip, flags, mac, iface = c[0], c[2], c[3].lower(), c[5]
        if int(flags, 16) & 2 == 0 or mac == "00:00:00:00:00:00":
            continue
        res[mac] = {"id": mac, "ip": ip, "mac": mac, "iface": iface}
    return res


def vizinhos_ip():
    res = {}
    try:
        p = subprocess.run(["ip", "neigh"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return res
    for linha in p.stdout.splitlines():
        c = linha.split()
        if "lladdr" not in c:
            continue
        estado = c[-1]
        if estado in ("FAILED", "INCOMPLETE", "NOARP"):
            continue
        ip = c[0]
        mac = c[c.index("lladdr") + 1].lower()
        iface = c[c.index("dev") + 1] if "dev" in c else "?"
        if mac == "00:00:00:00:00:00" or mac.startswith(("33:33", "01:00:5e", "ff:ff")):
            continue
        res[mac] = {"id": mac, "ip": ip, "mac": mac, "iface": iface}
    return res


# ----------------------------------------------------------------- amostrador
class Amostrador:
    """Produz "quadros" com taxas, conexões e vizinhos a cada chamada de amostrar()."""

    def __init__(self, fonte="auto", incluir_lo=False, incluir_loopback=False,
                 ifaces=None, intervalo_vizinhos=5.0):
        self.fonte = fonte
        self.incluir_lo = incluir_lo
        self.incluir_loopback = incluir_loopback
        self.ifaces = set(ifaces) if ifaces else None
        self.intervalo_vizinhos = intervalo_vizinhos
        self.tem_ss = shutil.which("ss") is not None
        self.tem_ip = shutil.which("ip") is not None
        self.fonte_usada = None
        self._t0 = time.monotonic()
        self._ult_t = None
        self._ult_if = None
        self._ult_con = None
        self._vizinhos = {}
        self._viz_t = -1e9
        self._viz_conhecidos = None

    def _usar_iface(self, nome):
        if self.ifaces is not None:
            return nome in self.ifaces
        if nome == "lo":
            return self.incluir_lo
        return not nome.startswith(PREFIXOS_IGNORADOS)

    def _conexoes(self):
        if self.fonte in ("auto", "ss") and self.tem_ss:
            try:
                r = conexoes_ss()
                self.fonte_usada = "ss"
                return r
            except (OSError, subprocess.SubprocessError):
                if self.fonte == "ss":
                    raise
        self.fonte_usada = "proc"
        return conexoes_proc()

    def _atualizar_vizinhos(self, agora):
        if agora - self._viz_t < self.intervalo_vizinhos:
            return
        self._viz_t = agora
        v = vizinhos_arp()
        if self.tem_ip:
            for mac, d in vizinhos_ip().items():
                v.setdefault(mac, d)
        self._vizinhos = v

    def amostrar(self):
        agora = time.monotonic()
        atual = ler_interfaces()
        dt = None if self._ult_t is None else max(1e-3, agora - self._ult_t)

        ent_b = sai_b = ent_p = sai_p = 0.0
        por_if = {}
        for nome, v in atual.items():
            if not self._usar_iface(nome):
                continue
            if self._ult_if is not None and nome in self._ult_if and dt:
                a = self._ult_if[nome]
                d = [max(0, v[i] - a[i]) for i in range(4)]  # contador reiniciado -> 0
                eb, ep, sb, sp = (x / dt for x in d)
                ent_b += eb; ent_p += ep; sai_b += sb; sai_p += sp
                por_if[nome] = {"entrada_bps": round(eb, 1), "saida_bps": round(sb, 1)}
            else:
                por_if[nome] = {"entrada_bps": 0.0, "saida_bps": 0.0}

        # conexões
        brutas = self._conexoes()
        chaves = {}
        for proto, lip, lp, rip, rp in brutas:
            if not self.incluir_loopback and (_e_loopback(rip) or rip == lip):
                continue
            chaves[(proto, lip, lp, rip, rp)] = classificar(lp, rp)
        por_classe = {c: 0 for c in ORDEM_CLASSES}
        for cl in chaves.values():
            por_classe[cl] += 1
        novas = []
        fechadas = 0
        if self._ult_con is not None:
            for k, cl in chaves.items():
                if k not in self._ult_con:
                    novas.append({"classe": cl, "proto": k[0], "remoto": k[3], "porta": k[4]})
            fechadas = sum(1 for k in self._ult_con if k not in chaves)
        self._ult_con = chaves

        # vizinhos
        self._atualizar_vizinhos(agora)
        lista = sorted(self._vizinhos.values(), key=lambda d: d["id"])
        ids = {d["id"] for d in lista}
        novos = []
        if self._viz_conhecidos is not None:
            novos = sorted(ids - self._viz_conhecidos)
        self._viz_conhecidos = ids

        self._ult_t = agora
        self._ult_if = atual
        return {
            "t": round(agora - self._t0, 3),
            "dt": round(dt if dt else 0.5, 3),
            "demo": False,
            "entrada_bps": round(ent_b, 1), "saida_bps": round(sai_b, 1),
            "entrada_pps": round(ent_p, 1), "saida_pps": round(sai_p, 1),
            "interfaces": por_if,
            "conexoes": {"total": len(chaves), "por_classe": por_classe,
                         "remotos": len({k[3] for k in chaves})},
            "novas": novas, "fechadas": fechadas,
            "vizinhos": lista, "novos_vizinhos": novos,
            "fonte": self.fonte_usada,
        }
