const DSA_REQUIRED_ROLE_KEYS = new Set([
	"software_engineer",
	"backend_python_developer",
	"backend_java_developer",
	"backend_node_developer",
	"frontend_react_developer",
	"full_stack_developer",
	"mobile_app_developer",
	"data_engineer",
	"machine_learning_engineer",
	"ai_engineer",
	"embedded_systems_engineer",
	"iot_engineer",
	"robotics_software_engineer",
	"site_reliability_engineer",
	"firmware_engineer",
]);

export function normalizeRoleKey(value) {
	return String(value || "").trim().toLowerCase();
}

export function roleRequiresDsa(roleKey) {
	const normalized = normalizeRoleKey(roleKey);
	if (!normalized) {
		return true;
	}
	return DSA_REQUIRED_ROLE_KEYS.has(normalized);
}