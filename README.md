# 🛡️ Hedging Operational Rig (H.O.R.)

**Hedging Operational Rig** è un sistema operativo di trading progettato per **neutralizzare il rischio direzionale** tramite **hedging strutturato**, ladder di short basata su **Bollinger Bands multi-timeframe** e gestione avanzata dell’**average entry**.
<img width="1897" height="978" alt="image" src="https://github.com/user-attachments/assets/1cac729f-0074-4a35-82be-c662f571bf69" />

Non è un bot automatico.
È un **rig operativo**: uno strumento di controllo, disciplina e difesa del capitale.

---

## 🎯 Obiettivo del progetto

* Ridurre l’esposizione direzionale su asset volatili (es. ETH)
* Gestire short **martingalati e controllati** su livelli strutturali
* Monitorare **average entry, size, PnL real-time**
* Evitare liquidazioni impulsive e decisioni emotive
* Lavorare in sinergia con una **posizione spot di lungo periodo**

---

## 🧠 Filosofia operativa (IMPORTANTISSIMO)

> **Hedging Operational Rig NON sostituisce lo spot.
> Lo protegge.**

### Strategia consigliata

1. **Holdare una parte del capitale in SPOT** (es. ETH in cold wallet)
2. Usare **Hedging Operational Rig** per:

   * Aprire **SHORT strutturati**
   * Ricaricare su livelli Bollinger crescenti
   * Gestire l’average entry in modo matematico
3. In caso di forte salita del prezzo:

   * Lo short va in drawdown
   * Lo spot aumenta di valore
   * Il rischio netto viene **neutralizzato**

👉 Questo tool è pensato per **hedging**, non per scommesse direzionali.

---

## ⚙️ Funzionalità principali

* ✅ LONG / SHORT selezionabile
* ✅ Leva configurabile (consigliata: **1x**)
* ✅ Max size totale (es. 2 ETH)
* ✅ Ladder di entry basata su **Bollinger Bands multi-timeframe**
* ✅ Ricarico geometrico (coefficiente `r` personalizzabile)
* ✅ FILLED “latched” (una volta fillato, resta fillato)
* ✅ Average Entry dinamico
* ✅ **PnL real-time**
* ✅ Alert con **popup + suono Windows**
* ✅ Alert continuo quando il prezzo tocca l’average entry
* ✅ **Dark Mode**
* ✅ Dashboard chiara e operativa

---

## 🖥️ Requisiti

* Python **3.10+**
* Sistema operativo: **Windows** (per notifiche sonore native)
* Connessione internet (API Binance public)

Dipendenze principali:

```bash
pip install requests
```

(Tkinter è incluso nelle installazioni standard di Python su Windows)

---

## 🚀 Avvio del software (RACCOMANDATO)

### ▶️ Python Launcher PRO

Per lanciare comodamente lo script (con gestione ambiente, log e riavvii):

🔗 [https://github.com/mikeminer/Python-Launcher-PRO](https://github.com/mikeminer/Python-Launcher-PRO)

**Consigliato** per:

* uso quotidiano
* avvio rapido
* evitare problemi di path / doppio click

---

## 📡 Segnali di ingresso SHORT (consigliato)

Hedging Operational Rig **non genera segnali di ingresso**:
è progettato per **gestire l’esecuzione e il rischio**, non per decidere *quando* entrare.

Per ricevere segnali di **SHORT** è fortemente consigliato abbinarlo a:

🔗 [https://github.com/mikeminer/tradAI](https://github.com/mikeminer/tradAI)

👉 Usa **tradAI** per:

* identificare contesto di short
* validare timing
* poi esegui la gestione con **H.O.R.**

---

## 🧩 Workflow consigliato

```text
[ tradAI ]
   ↓  (segnale SHORT)
[ Hedging Operational Rig ]
   ↓
Ladder Bollinger + gestione average
   ↓
Hedging contro spot
```

---

## ⚠️ Disclaimer

Questo software:

* ❌ **NON è un bot automatico**
* ❌ **NON garantisce profitti**
* ❌ **NON sostituisce la gestione del rischio personale**

È uno **strumento di supporto operativo** per trader consapevoli.
Usalo solo se comprendi:

* leve
* marginazione
* drawdown
* hedging
