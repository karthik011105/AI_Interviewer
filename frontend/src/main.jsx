import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import "./styles.css";

// The boundary sits inside BrowserRouter so its "Back to start" navigation has
// a router above it, and outside App so a throw anywhere in the tree is caught
// rather than blanking the page. Without one, React unmounts the whole tree on
// an unhandled render error and the user gets a white screen.
ReactDOM.createRoot(document.getElementById("root")).render(
	<React.StrictMode>
		<BrowserRouter>
			<ErrorBoundary>
				<App />
			</ErrorBoundary>
		</BrowserRouter>
	</React.StrictMode>,
);
