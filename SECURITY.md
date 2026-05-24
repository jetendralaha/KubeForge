# Security Policy

## Supported Versions

KubeForge is currently in early development. Security fixes are applied to the latest release line.

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅ |
| < 0.1   | ❌ |

## Reporting a Vulnerability

Please do **not** report security vulnerabilities through public GitHub issues.

Report vulnerabilities privately by emailing:

- security@kubeforge.dev

Include the following details where possible:

- A clear description of the issue
- Affected version(s) and deployment context
- Reproduction steps or proof of concept
- Potential impact
- Suggested mitigation (optional)

## Response Process

Maintainers aim to:

1. Acknowledge receipt within 72 hours
2. Assess severity and impact
3. Provide a remediation timeline
4. Release a fix and publish a security advisory when appropriate

## Security Best Practices for Users

- Keep KubeForge and dependencies up to date
- Protect API endpoints behind network controls
- Use least-privilege kubeconfig credentials
- Rotate and protect API keys (`KUBEFORGE_AI_API_KEY`)
- Validate generated manifests before production deployment
- Use signed images and trusted registries where possible

## Scope Notes

Typical in-scope areas:

- Authentication/authorization bypass
- Command injection and unsafe subprocess execution
- Deserialization or parsing vulnerabilities
- Secrets exposure in logs, API responses, or bundles

Out-of-scope unless severe:

- Missing best-practice hardening without exploit path
- Issues requiring privileged local machine compromise first
