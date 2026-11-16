"""Desmonta / remonta boot.img Android header v1 e v2 (Galaxy A51 = v2, Motorola One Vision kane = v1).

Formato (system/tools/mkbootimg/include/bootimg/bootimg.h do AOSP):
  header v0..v2: magic, kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, second_addr,
  tags_addr, page_size, header_version, os_version, name[16], cmdline[512], id[32], extra_cmdline[1024],
  (v1) recovery_dtbo_size, recovery_dtbo_offset, header_size, (v2) dtb_size, dtb_addr.
  Depois do header (1 página): kernel, ramdisk, second, recovery_dtbo, dtb — cada um alinhado à página.
A Samsung acrescenta, depois das imagens, "SEANDROIDENFORCE" + assinatura "SignerVer02" e um AVB footer
no fim da partição. Na Motorola (kane, header v1), o DTB vai no slot "second" e há um AVB footer no fim.
Em aparelhos A/B system-as-root (como o kane), o bootloader injeta "skip_initramfs" no boot normal;
defina PATCH_SKIP_INITRAMFS=1 ao empacotar para trocar "skip_initramfs" por "want_initramfs" no kernel.

Uso:
  python bootimg.py unpack <boot.img> <pasta>
  python bootimg.py pack <pasta> <novo-ramdisk> <saida.img> [tamanho_particao]
"""
import hashlib
import json
import os
import struct
import sys

HDR_V1 = "<8s10I16s512s32s1024sIQI"
HDR_V2 = "<8s10I16s512s32s1024sIQIIQ"


def unpack(path, out):
    b = open(path, "rb").read()
    f = struct.unpack_from(HDR_V2, b, 0)
    (magic, ks, ka, rs, ra, ss, sa, ta, ps, hv, osv, name, cmd, idb, xcmd, rdtbo_s, rdtbo_o, hsz, dtbs, dtba) = f
    assert magic == b"ANDROID!" and hv in (1, 2), (magic, hv)
    if hv == 1:
        dtbs, dtba = 0, 0
    pages = lambda n: (n + ps - 1) // ps
    off = ps
    parts = {}
    for nm, sz in (("kernel", ks), ("ramdisk", rs), ("second", ss), ("recovery_dtbo", rdtbo_s), ("dtb", dtbs)):
        parts[nm] = (off, sz)
        off += pages(sz) * ps
    os.makedirs(out, exist_ok=True)
    for nm, (o, sz) in parts.items():
        if sz:
            open(os.path.join(out, nm), "wb").write(b[o:o + sz])
    meta = dict(header_version=hv, kernel_addr=ka, ramdisk_addr=ra, second_addr=sa, tags_addr=ta, page_size=ps,
                recovery_dtbo_offset=rdtbo_o, os_version=osv, name=name.rstrip(b"\0").decode(),
                cmdline=cmd.rstrip(b"\0").decode(), extra_cmdline=xcmd.rstrip(b"\0").decode(),
                header_size=hsz, dtb_addr=dtba, sizes={k: v[1] for k, v in parts.items()},
                images_end=off, partition_size=len(b))
    tail = b[off:off + 64]
    meta["after_images"] = tail[:16].decode("latin1")
    json.dump(meta, open(os.path.join(out, "meta.json"), "w"), indent=2)
    print(json.dumps(meta, indent=2))


def pack(src, ramdisk_path, outp, part_size=None):
    m = json.load(open(os.path.join(src, "meta.json")))
    hv = m.get("header_version", 2)
    ps = m["page_size"]
    k = open(os.path.join(src, "kernel"), "rb").read()
    if os.environ.get("PATCH_SKIP_INITRAMFS") == "1":
        n_rep = k.count(b"skip_initramfs\x00")
        assert n_rep > 0, "skip_initramfs nao encontrado no kernel (talvez comprimido?)"
        k = k.replace(b"skip_initramfs\x00", b"want_initramfs\x00")
        print(f"kernel patch: skip_initramfs -> want_initramfs ({n_rep}x)")
    r = open(ramdisk_path, "rb").read()
    sp = os.path.join(src, "second")
    s = open(sp, "rb").read() if os.path.exists(sp) else b""
    dp = os.path.join(src, "dtb")
    d = open(dp, "rb").read() if (hv >= 2 and os.path.exists(dp)) else b""
    rp = os.path.join(src, "recovery_dtbo")          # só existe na imagem de recovery
    rd = open(rp, "rb").read() if os.path.exists(rp) else b""
    pages = lambda n: (n + ps - 1) // ps
    rd_off = (1 + pages(len(k)) + pages(len(r)) + pages(len(s))) * ps if rd else 0
    # id = SHA-1 igual ao mkbootimg: cada imagem seguida do seu tamanho (uint32 LE); v1 recovery_dtbo, v2 dtb
    h = hashlib.sha1()
    blobs = (k, r, s, rd, d) if hv >= 2 else (k, r, s, rd)
    for blob in blobs:
        h.update(blob)
        h.update(struct.pack("<I", len(blob)))
    if hv == 1:
        hdr = struct.pack(HDR_V1, b"ANDROID!", len(k), m["kernel_addr"], len(r), m["ramdisk_addr"],
                          len(s), m["second_addr"], m["tags_addr"], ps, 1, m["os_version"],
                          m["name"].encode(), m["cmdline"].encode(), h.digest(), m["extra_cmdline"].encode(),
                          len(rd), rd_off, m["header_size"])
    else:
        hdr = struct.pack(HDR_V2, b"ANDROID!", len(k), m["kernel_addr"], len(r), m["ramdisk_addr"],
                          len(s), m["second_addr"], m["tags_addr"], ps, 2, m["os_version"],
                          m["name"].encode(), m["cmdline"].encode(), h.digest(), m["extra_cmdline"].encode(),
                          len(rd), rd_off, m["header_size"], len(d), m["dtb_addr"])
    pad = lambda x: x + b"\0" * ((ps - len(x) % ps) % ps)
    img = pad(hdr) + pad(k) + pad(r) + (pad(s) if s else b"") + (pad(rd) if rd else b"") + (pad(d) if d else b"")
    size = int(part_size or m["partition_size"])
    tail_src = os.environ.get("SAMSUNG_TAIL_FROM") or os.environ.get("AVB_TAIL_FROM")
    if tail_src:
        img = add_samsung_tail(img, tail_src, m["images_end"], size)
    assert len(img) <= size, "imagem maior que a particao"
    img += b"\0" * (size - len(img))
    open(outp, "wb").write(img)
    print("ok:", outp, len(img), "bytes; kernel", len(k), "ramdisk", len(r), "second", len(s), "dtb", len(d))


def add_samsung_tail(img, orig_path, orig_images_end, size):
    """Copia do boot original (que o bootloader aceitou) o bloco Samsung opcional (SEANDROIDENFORCE + SignerVer02)
    e o vbmeta embutido + AVB footer, como o Magisk faz. O footer é reescrito com os novos offsets.
    O vbmeta embutido continua o original (assinado); o hash dele não bate com o boot novo, o mesmo caso
    do boot do Magisk, aceito com bootloader desbloqueado."""
    o = open(orig_path, "rb").read()
    foot = o[-64:]
    assert foot[:4] == b"AVBf"
    maj, mnr, orig_size, vb_off, vb_size = struct.unpack(">IIQQQ", foot[4:36])
    samsung = o[orig_images_end:orig_size]            # SEANDROIDENFORCE + assinatura (vazio em Motorola/AOSP)
    assert not samsung or samsung.startswith(b"SEANDROIDENFORCE")
    vbmeta = o[vb_off:vb_off + vb_size]
    gap = vb_off - orig_size                          # alinhamento usado pelo avbtool (2 KiB ou 4 KiB)
    body = img + samsung
    new_orig_size = len(body)
    new_vb_off = (new_orig_size + 4095) // 4096 * 4096
    assert new_vb_off + vb_size + 64 <= size, "sem espaco para o footer"
    out = bytearray(size)
    out[:len(body)] = body
    out[new_vb_off:new_vb_off + vb_size] = vbmeta
    nf = foot[:12] + struct.pack(">QQQ", new_orig_size, new_vb_off, vb_size) + foot[36:]
    out[-64:] = nf
    print(f"avb tail: extra={len(samsung)} B, vbmeta {vb_size} B @ {new_vb_off} (antes {vb_off}, gap {gap})")
    return bytes(out)


if __name__ == "__main__":
    if sys.argv[1] == "unpack":
        unpack(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "pack":
        pack(sys.argv[2], sys.argv[3], sys.argv[4], *(sys.argv[5:6]))
