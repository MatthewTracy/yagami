# See Yagami in action

Yagami routes requests to local or cloud models while keeping detected private
information on your machine under the default policy.

<video controls preload="none" playsinline style="width:100%;height:auto;border-radius:12px" poster="../images/capabilities-demo.png" aria-label="Yagami capabilities demo: local answers, private patient context and cloud image generation">
  <source src="https://github.com/MatthewTracy/yagami/releases/download/v0.8.5/yagami-0.8.5-capabilities-demo.mp4" type="video/mp4">
  Your browser does not support embedded video. Use the MP4 link below.
</video>

[Open or download the demo (MP4, 59 seconds)](https://github.com/MatthewTracy/yagami/releases/download/v0.8.5/yagami-0.8.5-capabilities-demo.mp4).
The recording is silent; captions explain the routing decisions.

## What the recording shows

- A natural-language question about sunlight, answered by a local model.
- A fictional patient chart-review request classified as sensitive and forced
  to local inference. The routing panel shows the decision.
- A new conversation requesting an image. Yagami selects the configured
  Stability AI provider and shows the cloud route.

The final screen shows how to install Yagami:

```bash
pip install yagami
```

## Run it yourself

For a quick local introduction, run `yagami demo`. It uses your configured
Ollama model if available, otherwise a clearly labeled policy-only fallback.
This mode deliberately disables cloud routing.

To use local and cloud models as shown in the video, run `yagami init`, configure
your local model and cloud providers, then start `yagami serve`. Cloud providers
require their own credentials and may charge for usage. See
[configuration](configuration.md) and [deployment](deployment.md).

## About this recording

Recorded with the published Yagami 0.8.5 package, Gemma 3 via Ollama for local
answers, and Stability AI for image generation. The patient information is
synthetic. Responses and routing decisions are real; captions and animated
yellow highlights were added for presentation.

The video demonstrates the configured policy; automated detection can miss
sensitive information. Use caller-declared sensitivity or local-only policies
for strict workflows. Local inference alone does not establish HIPAA
compliance. See the [threat model](threat-model.md).
