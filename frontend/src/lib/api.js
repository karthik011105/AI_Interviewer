export function buildApiHeaders(accessToken, headers = {}) {
	return accessToken
		? {
				...headers,
				Authorization: `Bearer ${accessToken}`,
			}
		: headers;
}