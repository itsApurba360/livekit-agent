# Research: Handling High Background Noise in Voice AI

## How Natively Built-in VAD Behaves in Noisy Settings
The built-in VAD of native Realtime APIs (Gemini Live and OpenAI Realtime) is **acoustic-based** at its core (analyzing frequency bands and volume levels):
* **The Problem**: In extremely noisy environments (loud traffic, call centers, sirens, crying babies), a simple acoustic VAD cannot tell the difference between the primary user speaking and background noise.
* **OpenAI's Semantic VAD**: OpenAI Realtime API uses a *semantic* model on top of VAD to see if the transcribed tokens make linguistic sense as a completed turn. However, if background noise is loud enough to register as voice activity, it will still trigger the voice-start detector, causing the AI agent to repeatedly interrupt itself.
* **The Result**: Relying *solely* on built-in VAD in highly noisy environments usually results in a bad user experience (the agent interrupts itself, turns are held open indefinitely by background sounds, or the agent responds to background conversations).

---

## AI-Driven Solutions: Neural Noise Suppression & Voice Isolation
To achieve human-like ignoring of background noise, modern voice architectures apply **Neural Noise Suppression (NNS)** or **Voice Isolation** to the raw audio stream *before* it reaches the VAD and the LLM. 

LiveKit provides native integrations for these solutions:

### 1. AI Noise Cancellation (e.g., `ai-coustics` or `Krisp`)
Instead of just filtering simple frequencies (like traditional DSP noise gates), these models use deep neural networks trained on millions of hours of clean vs. noisy speech:
* **Background Noise Suppression**: Removes steady-state or non-voice noises (fans, mouse clicks, keyboard taps, traffic, background music) while keeping all human speech intact.
* **Voice Isolation (Target Speaker Extraction)**: Isolates the *primary speaker's voice* (closest to the microphone) and completely silences other human voices speaking in the background (like crosstalk in a call center).
* **ASR Optimization**: Models like `ai-coustics` are trained specifically to clean the audio while keeping the phonetic details intact, preventing degradation in speech recognition (STT) accuracy.

### 2. LiveKit's Adaptive Interruption Handling
LiveKit includes an adaptive interruption handling model that runs in the background. It distinguishes between a user intentionally speaking to interrupt the agent vs. incidental background sounds (a throat-clear, a dog bark, or a brief background noise). It prevents the agent from stopping speech unless a genuine user turn is detected.

---

## How It Is Implemented in Your Codebase
Your repository is already configured to use the **`ai-coustics`** AI noise cancellation plugin!

In [agent.py](file:///Users/pankajsankhla/code/livekit_agent/agent.py#L537-L544), when `"noise_cancellation": true` is enabled in `agent_config.json`, the agent applies the `QUAIL_VF_S` (Voice Focus) model:
```python
if agent_config.get("noise_cancellation", False):
    try:
        from livekit.plugins import ai_coustics

        nc_option = ai_coustics.audio_enhancement(model=ai_coustics.EnhancerModel.QUAIL_VF_S)
    except ImportError as err:
        logger.warning("Noise cancellation disabled; ai_coustics plugin unavailable: %s", err)
```
This cleans the incoming audio stream from the room before it is sent to the Gemini Live API. Because of this, Gemini's built-in VAD only receives a cleaned, noise-free voice stream, allowing it to work effectively even in noisy rooms.
