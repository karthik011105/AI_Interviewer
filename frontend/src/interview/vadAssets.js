/**
 * Where MicVAD finds its model and onnxruntime's WASM engine.
 *
 * vite.config.js serves these from the site root (see the vad-assets plugin).
 * onnxruntime otherwise resolves its WASM next to its own module URL — in
 * development that is Vite's dependency cache, where the files do not exist —
 * so every MicVAD.new() failed with "no available backend found" and both
 * interview screens fell back to typed answers.
 *
 * Shared by every MicVAD.new() call so the two screens cannot drift apart.
 */
export const VAD_ASSET_OPTIONS = {
	workletURL: "/vad.worklet.bundle.min.js",
	modelURL: "/silero_vad.onnx",
	ortConfig: (ort) => {
		ort.env.wasm.wasmPaths = "/";
	},
};
