# Motorola One Vision (`XT1970-1` / `kane`): Guia Completo e Auditoria de Hardware

Este documento detalha o processo completo de transformação do **Motorola One Vision (`XT1970-1`, codinome `kane` / `kane_sprout`)** em um **servidor Debian 13 (Trixie) ARM64 nativo**, utilizando a arquitetura do repositório **Mobile Server OS** (`runit` como PID 1, zero Android em execução, console serial USB de emergência e painel web).

---

## 1. Comparativo de Arquitetura: Galaxy A51 (`SM-A515F`) vs Motorola One Vision (`XT1970-1 / kane`)

Apesar de ser um aparelho Motorola (programa *Android One*), o **Motorola One Vision (`kane`)** é construído sobre o SoC **Samsung Exynos 9609 (`exynos9610`)**, compartilhando quase toda a infraestrutura de baixo nível do Samsung Galaxy A51 (`exynos9611`):

| Componente | Samsung Galaxy A51 (`SM-A515F`) | Motorola One Vision (`XT1970-1` / `kane`) | Impacto e Adaptação |
|---|---|---|---|
| **SoC** | Samsung Exynos 9611 (`universal9611`) | Samsung Exynos 9609 (`exynos9610` / `erd9610`) | 4× Cortex-A73 (2,2 GHz) + 4× Cortex-A53 (1,6 GHz), ARMv8.0-A (`aarch64`) |
| **RAM** | 4 GB LPDDR4x | 4 GB LPDDR4x (`SAMSUNG LP4x`) | **~3,4 GB livres** em modo Debian nativo (~370 MB usados com todos os 10 serviços) |
| **Armazenamento (UFS)** | 128 GB UFS (`13520000.ufs`) | 128 GB UFS (`SAMSUNG KLUDG4U1EA-B0C1`, `13520000.ufs`) | Mesmo endereço de controlador UFS; `userdata` em `/dev/sda42` (`f2fs`, 111 GB livres) |
| **Controlador USB** | Synopsys DWC3 (`13200000.dwc3`) | Synopsys DWC3 (`13200000.dwc3`) | Mesmo controlador e sysfs `/sys/class/android_usb/android0` |
| **Gadget USB de Rede** | `CONFIG_USB_CONFIGFS_NCM=y` (`ncm0`) | `CONFIG_USB_CONFIGFS_RNDIS=y` (`rndis0`) | `initramfs/init` detecta automaticamente `ncm.0` ou `rndis.0` + `acm.0` (`ttyGS0`) |
| **Wi-Fi / Bluetooth** | Samsung S.LSI SCSC (`scsc_wlbt`, `mx140.bin`) | Samsung S.LSI SCSC (`scsc_wlbt`, `mx140.bin`) | Mesmo driver; firmware em `/vendor/etc/wifi/mx140.bin` (copiado de `vendor_b`) |
| **Zonas Térmicas** | `BIG`, `LITTLE`, `G3D`, `ISP`, `battery` | `BIG`, `LITTLE`, `G3D`, `ISP`, `battery` | Nomes idênticos em `/sys/class/thermal/thermal_zone0..4` |
| **Bootloader** | Samsung S-Boot (Download Mode / Odin) | Motorola `MBM-2.2` (`0xBA09`, Fastboot nativo) | **Não requer Odin nem Windows**: gravação feita 100% via `fastboot` (macOS/Linux/Windows) |
| **Esquema de Partições** | Single-slot (`boot` e `recovery` separados) | **A/B (`_a` e `_b`)**, `system-as-root` | **Dual-boot seguro**: Debian nativo no `slot_a` (`boot_a`) e Android 11 original intacto no `slot_b` |
| **Formato `boot.img`** | Android Boot Image **Header v2** (DTB em `dtb`) | Android Boot Image **Header v1** (`page_size=2048`, DTB em `second`) | Suportado nativamente pelo `tools/bootimg.py` atualizado (`hv in (1, 2)`) |
| **`CONFIG_DEVTMPFS`** | `CONFIG_DEVTMPFS=y` | `# CONFIG_DEVTMPFS is not set` | `initramfs/init` e `/etc/runit/1` usam `tmpfs` em `/dev` + `busybox mdev -s` |
| **Kernel Usado** | Stock `4.14.113` | LineageOS 24.0 `4.14.357-openela-g0c019791d5ef` | Inclui `CONFIG_VETH=y`, `CONFIG_OVERLAY_FS=y` e remove `CONFIG_ANDROID_PARANOID_NETWORK` |

---

## 2. Mapa de Partições Críticas do `kane` (`13520000.ufs`)

| Partição | Dispositivo de Bloco | Tamanho | Papel e Cuidados |
|---|---|---|---|
| `persist` / `persist2` | `/dev/sda2` / `/dev/sda4` | 32 MB / 8 MB | Calibração e dados persistentes (`ext4`, montado somente leitura) |
| `efs` | `/dev/sda3` | 20 MB | **CRÍTICO (IMEI/Calibração de rádio)** — fazer backup antes de tudo; montar sempre `ro` |
| `misc` | `/dev/sda9` | 1 MB | Bootloader Control Block (BCB) |
| `slotinfo` | `/dev/sda11` | 1 MB | Metadados dos slots A/B (`slot_a` e `slot_b`) |
| `boot_a` / `boot_b` | `/dev/sda20` / `/dev/sda21` | 64 MB cada | Kernel + initramfs + DTB (`second`). `boot_a` = Debian nativo; `boot_b` = Android 11 original |
| `dtbo_a` / `dtbo_b` | `/dev/sda22` / `/dev/sda23` | 1 MB cada | Device Tree Overlays |
| `vbmeta_a` / `vbmeta_b` | `/dev/sda28` / `/dev/sda29` | 64 KB cada | Android Verified Boot (`vbmeta_a` com `flags=3` para permitir boot customizado no `slot_a`) |
| `vendor_a` / `vendor_b` | `/dev/sda37` / `/dev/sda38` | 512 MB cada | Contém `/etc/wifi/mx140.bin` e `/firmware/` do driver Wi-Fi `scsc_wlbt` |
| `userdata` | `/dev/sda42` | 111 GB | Formatada em `f2fs`; armazena o Debian em `/data/debian-server/rootfs` |

---

## 3. Lições Aprendidas e Armadilhas Resolvidas no `kane`

### 3.1. Conexão USB no Mac mini (Apple Silicon) e Cabos USB-C E-Marked
- **Problema**:
  1. Nas portas USB-C frontais do Mac mini (`Port-USB-C@6`), o modo Fastboot do `kane` falha ao enumerar (`kPortStatAddressFailureCount`).
  2. Com cabos USB-C para USB-C ativos (*E-Marked*, como o cabo trançado da Apple), espetar o cabo a frio na tela de Fastboot não acorda a pilha USB do bootloader `MBM-2.2`, e espetar com o Android ligado nas portas Thunderbolt traseiras causa loop de *Dual-Role Port (DRP)* (`ConnectionCount` subindo continuamente).
- **Solução**: Conectar o celular através de um **Hub USB 2.0 externo** usando um cabo **USB-A para USB-C**. O pull-up fixo do conector USB-A elimina a negociação USB-C PD *Dual-Role* e torna tanto o `adb`, quanto o `fastboot` e o console serial `/dev/cu.usbmodemA51SERVER1` 100% estáveis.

### 3.2. `fastboot boot` Desativado e Ordem Exata do `vbmeta` no `MBM-2.2`
- O bootloader `MBM-2.2` da Motorola **não suporta** `fastboot boot imagem.img` (`Booting FAILED (remote: '')`) nem `fastboot reboot recovery` (`Unknown command`).
- Além disso, gravar `boot_a` imediatamente após `fastboot flash vbmeta_a` falha com:
  ```text
  Writing 'boot_a' (bootloader) validation image failed
  FAILED (remote: '')
  ```
- **Por quê**: O `MBM-2.2` mantém em RAM o estado de verificação AVB lido no início do boot. É obrigatório executar `fastboot reboot bootloader` **depois** de gravar `vbmeta_a` (com `flags=3` já assinado, sem passar `--disable-verity` no `fastboot` para não corromper a assinatura da imagem `lineage-vbmeta.img`) e **antes** de gravar `boot_a`:
  ```sh
  fastboot --set-active=a
  fastboot flash vbmeta_a lineage-vbmeta.img
  fastboot flash dtbo_a lineage-dtbo.img
  fastboot reboot bootloader
  fastboot flash boot_a lineage-boot.img
  ```

### 3.3. Como Entrar no Fastboot a Partir do Boot Normal na Motorola
- Ao forçar o reinício segurando **Power + Volume Menos (`-`)** por ~10 segundos, **se o botão Power continuar pressionado quando a tela apagar**, o bootloader ignora o Volume Menos e faz o boot normal.
- **Procedimento correto**: Segurar **Power + Volume Menos (`-`)** até a tela apagar e, **no exato instante em que a tela ficar preta, soltar o botão Power**, mantendo **apenas o Volume Menos (`-`)** pressionado por 2 segundos até surgir a tela do Fastboot.

### 3.4. Patch `skip_initramfs -> want_initramfs` (`PATCH_SKIP_INITRAMFS=1`)
- Por ser um dispositivo A/B `system-as-root`, o bootloader `MBM-2.2` injeta `skip_initramfs` na linha de comando do kernel (`/proc/cmdline`) em todo boot normal (omitindo-o apenas quando iniciado via menu `Recovery Mode`).
- Como o kernel no `boot.img` do `kane` é uma imagem ARM64 descomprimida (`Image`, header `ARMd`), o `tools/bootimg.py` com `PATCH_SKIP_INITRAMFS=1` substitui a string `skip_initramfs\x00` por `want_initramfs\x00` dentro do binário do kernel. Assim, o kernel **sempre** executa o `/init` do nosso `initramfs` no boot normal do `slot_a`.

### 3.5. Ausência de `CONFIG_DEVTMPFS` no Kernel do `kane`
- Diferente do kernel do Galaxy A51 (`CONFIG_DEVTMPFS=y`), o kernel do `kane` tem `# CONFIG_DEVTMPFS is not set` (no Android, o `ueventd` cria os nós em `/dev` sobre `tmpfs`).
- Sem `devtmpfs`, `mount -t devtmpfs devtmpfs /dev` falha e `/dev` fica sem `/dev/sda42` e sem `/dev/ttyGS0`.
- **Solução implementada em `initramfs/init` e `rootfs-overlay/etc/runit/1`**:
  - Se `mount -t devtmpfs` falhar, monta-se `tmpfs` em `/dev` e executa-se **`busybox mdev -s`** para criar automaticamente todos os dispositivos de bloco e caractere a partir de `/sys`.
  - Ajustam-se as permissões `chmod 666 /dev/null /dev/zero /dev/full /dev/random /dev/urandom /dev/tty /dev/ptmx` e `ptmxmode=666` em `/dev/pts` para que serviços não-root (`glances`, `httpd`) e sessões `sshd` funcionem perfeitamente.

---

## 4. Passo a Passo Resumido para Reproduzir no Motorola One Vision (`kane`)

### Passo 1: Desbloqueio do Bootloader
1. No Android, ative **Desbloqueio de OEM** e **Depuração USB** nas Opções do Desenvolvedor.
2. Reinicie no Fastboot (`adb reboot bootloader`) e obtenha a string de desbloqueio:
   ```sh
   fastboot oem get_unlock_data
   ```
3. Junte as 4 linhas em uma única string sem espaços no portal de desbloqueio da Motorola para receber o código de 20 caracteres e execute duas vezes:
   ```sh
   fastboot oem unlock <SEU_CODIGO>
   fastboot oem unlock <SEU_CODIGO>
   ```

### Passo 2: Subir o Recovery de Manutenção no `slot_a` (Preservando o Android Original no `slot_b`)
1. Grave o `vbmeta`, `dtbo` e `boot` do LineageOS 24.0 (`kane`) no `slot_a`:
   ```sh
   fastboot --set-active=a
   fastboot flash vbmeta_a lineage-vbmeta.img
   fastboot flash dtbo_a lineage-dtbo.img
   fastboot reboot bootloader
   fastboot flash boot_a lineage-boot.img
   ```
2. Na tela do Fastboot, pressione **Volume Menos (`-`)** até aparecer **`Recovery Mode`** e aperte **Power**.
3. No menu do LineageOS Recovery: **Advanced → Enable ADB**.

### Passo 3: Backup das Partições Críticas e Instalação do Rootfs Debian 13
1. Faça backup de `efs` (`/dev/block/by-name/efs`), `persist`, `boot_b`, `dtbo_b` e `vbmeta_b` via `adb pull`.
2. Formate `userdata` em `f2fs` e extraia o `debian:trixie-slim` (`arm64`) em `/data/debian-server/rootfs`:
   ```sh
   adb shell mkfs.f2fs -f /dev/block/by-name/userdata
   adb shell mount -t f2fs /dev/block/by-name/userdata /data
   adb shell mkdir -p /data/debian-server/rootfs
   ```
3. Copie o firmware Wi-Fi da partição `vendor_b` (`/dev/block/by-name/vendor_b`) para `/data/debian-server/rootfs/vendor/etc/wifi` e `/data/debian-server/rootfs/vendor/firmware`.
4. Via `chroot` (usando proxy HTTP por `adb reverse tcp:8123 tcp:8123`), instale os pacotes do Debian 13, copie `rootfs-overlay/` e `dashboard/index.html`, e gere o `initramfs` com `scripts/debian/build-initramfs.sh`.

### Passo 4: Empacotar e Gravar o `boot-kane-native.img`
```sh
python3 tools/bootimg.py unpack lineage-boot.img unpacked-lineage
PATCH_SKIP_INITRAMFS=1 AVB_TAIL_FROM=lineage-boot.img \
  python3 tools/bootimg.py pack unpacked-lineage initramfs-kane-prod.cpio.gz boot-kane-native.img
```
Grave em `boot_a` (via `dd if=boot-kane-native.img of=/dev/block/by-name/boot_a bs=1M` no Recovery ou `fastboot flash boot_a boot-kane-native.img` no Fastboot) e reinicie.

---

## 5. Acesso, Operação e Reversão (Voltar ao Android)

- **SSH via Wi-Fi**:
  ```sh
  ssh -i kane-work/id_ed25519_kane server@<IP_DO_CELULAR>
  ```
- **Painel de Status na Tela Física (`fbstatus` / `/dev/fb0`)**:
  - Serviço supervisionado pelo `runit` ([rootfs-overlay/etc/sv/fbstatus/run](file:///Users/amazingfood/Documents/GitHub/mobile-server-os/rootfs-overlay/etc/sv/fbstatus/run) e [rootfs-overlay/usr/local/sbin/a51-fbstatus](file:///Users/amazingfood/Documents/GitHub/mobile-server-os/rootfs-overlay/usr/local/sbin/a51-fbstatus)) que desenha diretamente no framebuffer (`1080×2520`, 32 bpp BGRA) a cada 5 segundos com métricas de rede, CPU, RAM, disco UFS, temperaturas, bateria e os 11 serviços `runit`.
  - **Controles pelos botões físicos do aparelho (`/dev/input/event6` / `gpio_keys`)**:
    - **Botão Power**: liga / desliga a tela física (para economizar energia quando não estiver olhando).
    - **Botão Volume `+` / `-`**: aumenta / diminui o brilho do display.
- **Painel Web (Dashboard)**:
  - `http://<IP_DO_CELULAR>/` (painel em tempo real na porta 80)
  - `http://<IP_DO_CELULAR>:61208/api/4/...` (API REST do Glances)
- **Console Serial de Emergência pelo Cabo USB**:
  - No macOS: `/dev/cu.usbmodemA51SERVER1` (`115200 8N1`, login root automático)
  - No Linux: `/dev/ttyACM0`
- **Reiniciar / Desligar com Segurança**:
  - `sudo a51-reboot` (reinicia no Debian nativo)
  - `sudo a51-reboot poweroff` (desliga o aparelho)
  - `sudo a51-reboot bootloader` (reinicia no Fastboot)
- **Como Voltar ao Android 11 Original (Dual-Boot `slot_b`)**:
  Como todo o Android 11 original da Motorola foi preservado intacto no `slot_b`, basta entrar no Fastboot e trocar o slot ativo:
  ```sh
  fastboot --set-active=b
  fastboot reboot
  ```
  *(Nota: se quiser usar o Android completo novamente, formate `userdata` com `fastboot -w` para que o Android recrie a estrutura criptografada padrão).*
