# Seedance 2.0 reference media

Submits up to nine ordered image references, optionally one video reference,
and one exact prompt to fal.ai's
`bytedance/seedance-2.0/reference-to-video` endpoint. Repeat
`--input image_ref=/path/to/frame.png` in the intended order.

The executor stages all experiment provenance locally and omits credentials
and signed URLs from durable output.
