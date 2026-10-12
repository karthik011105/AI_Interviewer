/**
 * PrepForge mark: a "P" whose bowl is a speech bubble and whose stem ends in
 * the bubble's tail - interview conversation and the brand initial in one
 * flat shape. Deliberately one colour, no gradient or glow, so it holds up at
 * 16px, in a single ink and on a business card.
 */
export function BrandMark({ size = 34, className = "" }) {
	return (
		<svg className={`brand-mark ${className}`} width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
			<path
				fillRule="evenodd"
				d="M11 7.5C11 6.67 11.67 6 12.5 6H27C35.28 6 42 12.72 42 21C42 29.28 35.28 36 27 36H22V41.2C22 42.4 20.6 43.07 19.67 42.32L11.56 35.83C11.2 35.55 11 35.12 11 34.66V7.5ZM22 15V27H26.5C29.81 27 32.5 24.31 32.5 21C32.5 17.69 29.81 15 26.5 15H22Z"
				fill="currentColor"
			/>
		</svg>
	);
}

export default function BrandLogo({ size = 34, showName = true, caption = "", className = "" }) {
	return (
		<span className={`brand-logo ${className}`}>
			<BrandMark size={size} />
			{showName ? (
				<span className="brand-logo__text">
					<span className="brand-logo__name">
						prep<span className="brand-logo__forge">forge</span>
					</span>
					{caption ? <span className="brand-logo__caption">{caption}</span> : null}
				</span>
			) : null}
		</span>
	);
}
