import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const root = path.dirname(fileURLToPath(import.meta.url));
const vadDist = path.join(root, "node_modules/@ricky0123/vad-web/dist");
const ortDist = path.join(root, "node_modules/onnxruntime-web/dist");

// @ricky0123/vad-web resolves its worklet, the Silero model, and onnxruntime's
// WASM files from the SITE ROOT (/vad.worklet.bundle.min.js, /silero_vad.onnx,
// /ort-wasm-*.wasm). Nothing ever put them there, so every request 404'd and
// MicVAD.new() threw — which the interview screens report as the generic
// "Microphone unavailable", for every user on every browser.
//
// Served from our own origin, not a CDN, so the production CSP can stay at
// script-src 'self' (see frontend/Dockerfile).
const VAD_ASSETS = {
	"vad.worklet.bundle.min.js": path.join(vadDist, "vad.worklet.bundle.min.js"),
	"silero_vad.onnx": path.join(vadDist, "silero_vad.onnx"),
	...Object.fromEntries(
		["ort-wasm-simd-threaded.wasm", "ort-wasm-simd-threaded.mjs"].map((name) => [
			name,
			path.join(ortDist, name),
		]),
	),
};

const CONTENT_TYPES = {
	".js": "text/javascript",
	".mjs": "text/javascript",
	".wasm": "application/wasm",
	".onnx": "application/octet-stream",
};

function vadAssets() {
	return {
		name: "vad-assets",
		configureServer(server) {
			server.middlewares.use((req, res, next) => {
				const name = decodeURIComponent((req.url || "").split("?")[0].replace(/^\//, ""));
				const file = VAD_ASSETS[name];
				if (!file) return next();
				res.setHeader("Content-Type", CONTENT_TYPES[path.extname(name)] || "application/octet-stream");
				fs.createReadStream(file).pipe(res);
			});
		},
		generateBundle() {
			for (const [fileName, file] of Object.entries(VAD_ASSETS)) {
				this.emitFile({ type: "asset", fileName, source: fs.readFileSync(file) });
			}
		},
	};
}

export default defineConfig({
	plugins: [react(), vadAssets()],
	resolve: {
		alias: [
			// vad-web imports the full onnxruntime-web, whose WebGPU build loads a
			// 26.5 MB WASM — over Cloudflare Pages' 25 MiB per-file limit, and
			// pointless for a ~2 MB VAD model. The CPU-only entry loads 13.3 MB.
			{ find: /^onnxruntime-web$/, replacement: "onnxruntime-web/wasm" },
		],
	},
	server: {
		// "localhost", not 127.0.0.1: Google sign-in only accepts localhost as a
		// JavaScript origin in development.
		host: "localhost",
		port: 5173,
		proxy: {
			"/api": {
				target: "http://127.0.0.1:8000",
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/api/, ''),
			},
		},
	},
});
