# 🎵 Bluetooth Mini-Player (Windows A2DP Audio Sink)

Un mini-reproductor de escritorio moderno, ligero y universal para Windows 10/11 que convierte tu PC en un receptor de audio Bluetooth de alta fidelidad (**A2DP Sink**). Escucha la música, podcasts o videos de tu celular en tus audífonos/parlantes de la computadora, con controles multimedia, metadatos en tiempo real y pausa en el dispositivo.

![Windows](https://img.shields.io/badge/Windows-10%20%2F%2011-0078D6?style=flat&logo=windows)
![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python)
![Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Pure%20WinRT)-brightgreen)
![License](https://img.shields.io/badge/License-MIT-blue)

---

## 🌟 Características Principales

* 🎧 **Audio Bluetooth de Alta Fidelidad (A2DP Sink):** Enruta el audio de cualquier teléfono (Android o iPhone) o tablet a los audífonos o parlantes de tu PC con latencia ultrabaja usando las APIs nativas de Windows Runtime (`AudioPlaybackConnection`).
* 📱 **Compatibilidad Universal con Cualquier Dispositivo:**
  * **Android (Samsung, Xiaomi/HyperOS, Motorola, Google Pixel, etc.):** Sincronización automática de metadatos (canción, artista, álbum, app) y control de pistas por ADB (USB o Wi-Fi sin cables).
  * **iOS (iPhone / iPad):** Integración con Enlace Móvil (Phone Link), Apple Media Service (AMS) por Bluetooth Low Energy (BLE) y pausa automática por interrupción de audio.
  * **Cualquier dispositivo Bluetooth:** Selector dinámico en la barra superior para alternar fácilmente entre varios teléfonos vinculados.
* ⏸ **Pausa Real en el Dispositivo:** Al pulsar el botón `⏸` en la PC, la reproducción se detiene directamente en el teléfono (evitando que la canción siga corriendo en silencio).
* 🛡 **Atenuación Inteligente para Microsoft Teams (Auto-Ducking):** Detecta cuando estás en una llamada o reunión de Teams y silencia automáticamente la música de tu teléfono; al colgar la llamada, reanuda el audio suavemente.
* 🌊 **Visualizador de Ondas Reactivo:** Gráfica de espectro reactiva al volumen y pico de audio real (WASAPI peak meter).
* 📲 **Control Remoto desde Móvil o Red Wi-Fi:** Al ejecutarse, abre la interfaz localmente y genera una URL de red local (`http://192.168.x.x:8765`) para que puedas controlar la reproducción desde el navegador de tu celular o tablet.
* 🚀 **Cero Dependencias Externas:** No requiere instalar librerías pesadas en Python; aprovecha las capacidades estándar del sistema y PowerShell WinRT.

---

## 📋 Requisitos

* **Sistema Operativo:** Windows 10 (versión 2004 o superior) o Windows 11.
* **Bluetooth:** Adaptador Bluetooth activo en la PC (integrado o USB dongle).
* **Python:** Python 3.10 o superior instalado.

---

## 🚀 Inicio Rápido

1. **Clona este repositorio:**
   ```bash
   git clone https://github.com/<tu-usuario>/bt-miniplayer.git
   cd bt-miniplayer
   ```

2. **Inicia el reproductor con 1 clic:**
   * Haz doble clic en el archivo **`iniciar_reproductor.bat`**, o ejecútalo por terminal:
     ```bash
     python app.py
     ```
   * Se abrirá automáticamente la ventana del reproductor en tu navegador: `http://localhost:8765`.

---

## 📱 Guía de Conexión por Dispositivo

### 1. Vincular por Bluetooth (Paso Inicial para Cualquier Dispositivo)
1. En Windows, ve a **Configuración → Dispositivos → Bluetooth** y empareja tu celular.
2. Abre el Mini-Reproductor y en el menú desplegable superior selecciona tu dispositivo.
3. Haz clic en **«🔗 Conectar Enlace de Audio Bluetooth»**. ¡Listo! Ya escucharás el sonido de tu teléfono en tus audífonos.

---

### 2. Sincronizar Canción, Artista y Controles de Pista

El mini-reproductor ofrece múltiples vías diseñadas para que funcione con cualquier dispositivo:

#### Opción A: Cualquier Android (Samsung, Xiaomi, Motorola, Pixel, etc.) vía ADB
Permite ver el nombre exacto de la canción, artista y controlar Anterior/Siguiente/Pausa en apps como Spotify, YouTube Music, Apple Music, TikTok, etc.

1. En tu teléfono, activa **Opciones de desarrollador**:
   * *Samsung / Motorola / Pixel:* Ajustes → Información del teléfono → Toca 7 veces en *Número de compilación*.
   * *Xiaomi / POCO:* Ajustes → Sobre el teléfono → Toca 7 veces en *Versión de MIUI / HyperOS*.
2. Activa **Depuración USB** (y en Xiaomi activa también *Depuración USB - Ajustes de seguridad*).
3. Conecta el teléfono por cable USB a la PC una vez y en la interfaz web pulsa:  
   **«🔌 Pasar Android conectado por USB a Wi-Fi Automático»**.  
   *¡A partir de ahí funciona inalámbricamente por Wi-Fi sin necesidad de cables!*

#### Opción B: iPhone o Android con «Enlace Móvil» (Phone Link) de Windows
Windows 10/11 incluye de fábrica la app **Enlace Móvil (Phone Link)**, que ahora es totalmente compatible tanto con iPhone como con Android:
1. Abre *Enlace Móvil* en Windows y vincula tu teléfono.
2. El Mini-Reproductor detectará automáticamente los controles multimedia de Windows (SMTC) mostrando la canción en vivo.

#### Opción C: Pausa Universal por Desconexión A2DP
Si no deseas activar ADB ni usar Enlace Móvil, deja activada la opción **«📱 Pausar canción en el dispositivo al pulsar ⏸»**:
* Al pausar en la interfaz de la PC, se suspende la conexión de audio Bluetooth, lo que desencadena de inmediato el evento nativo de desconexión de audífonos (`ACTION_AUDIO_BECOMING_NOISY` en Android o interrupción de `AVAudioSession` en iOS), pausando la música en tu teléfono al instante.

---

## ⌨️ Atajos de Teclado en la PC

| Tecla | Acción |
| :--- | :--- |
| <kbd>Espacio</kbd> | Alternar Play / Pausa |
| <kbd>→</kbd> | Siguiente canción |
| <kbd>←</kbd> | Canción anterior |
| <kbd>↑</kbd> | Subir volumen (+5%) |
| <kbd>↓</kbd> | Bajar volumen (-5%) |

---

## 🛠 Arquitectura Técnica

```
┌────────────────────────────────────────────────────────┐
│             Navegador / UI Mini-Player                 │
│         (HTML5, CSS3, ES6, Visualizador SVG)           │
└───────────────────────────▲────────────────────────────┘
                            │ HTTP JSON API (Port 8765)
┌───────────────────────────▼────────────────────────────┐
│                    Python HTTP Server                  │
│       - Gestor de estado y configuración persistente   │
│       - Daemon de detección ADB (USB/Wi-Fi mDNS)       │
│       - Auto-descargador de Google Platform-Tools      │
└───────────────────────────▲────────────────────────────┘
                            │ Subprocess Stdin/Stdout JSON
┌───────────────────────────▼────────────────────────────┐
│              WinRT & WASAPI PowerShell Bridge          │
│   • Windows.Media.Audio.AudioPlaybackConnection (A2DP) │
│   • Windows.Devices.Radios.Radio (Control Bluetooth)   │
│   • Windows.Media.Control (SMTC Global Session)        │
│   • CoreAudio / WASAPI Audio Endpoint Volume & Peak    │
│   • GATT BLE (Apple Media Service AMS & GMCS)          │
└────────────────────────────────────────────────────────┘
```

---

## 📄 Licencia

Este proyecto está bajo la licencia [MIT](LICENSE).
