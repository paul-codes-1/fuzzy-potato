# SSO (SAML 2.0) Setup Guide

CivicLens supports SAML 2.0 Single Sign-On for Enterprise customers. SSO allows your organization's users to authenticate through your existing identity provider (IdP) -- Okta, Azure AD, Google Workspace, or any SAML 2.0-compliant IdP.

## Prerequisites

- **Enterprise plan** -- SSO is available exclusively on the Enterprise plan.
- **Tenant-scoped admin credential** -- Use the tenant's API key, or an SSO browser session with the `admin` role.
- **IdP admin access** -- You'll need to create a SAML application in your IdP.

## Environment Variables

Set these in your `.env` file or deployment environment:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JWT_SECRET` | **Yes** (production) | Auto-generated | Secret key for signing JWT session tokens. Must be consistent across restarts and instances. Use a 64+ character random string. |
| `SP_BASE_URL` | **Yes** (production) | `https://app.civiclens.ai` | The public-facing base URL of your CivicLens instance. Used to construct SAML ACS and metadata URLs. |
| `SAML_DEBUG` | No | `false` | Set to `true` to enable verbose SAML logging for troubleshooting. |

## Architecture

SSO coexists with API key authentication:

- **API keys** (`X-API-Key` header) -- for programmatic access (scripts, integrations, widgets)
- **JWT Bearer tokens** (`Authorization: Bearer <token>`) -- for browser-based SSO sessions

After a successful SAML login, CivicLens issues a JWT session token (8-hour expiry). The JWT is accepted on all API endpoints that currently accept API keys.

Current implementation note: tenant API keys are still coarse-grained tenant credentials. In practice, the tenant API key can configure SSO and manage SSO users without a separate per-user approval flow.

## SP (Service Provider) Values

When configuring the SAML application in your IdP, use these values:

| Field | Value |
|-------|-------|
| **ACS URL** (Assertion Consumer Service) | `https://<your-domain>/api/v1/sso/acs` |
| **Entity ID** (SP Entity ID / Audience) | Configured per-tenant (e.g., `https://app.civiclens.ai/saml/<tenant_id>`) |
| **NameID Format** | `urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress` |
| **SP Metadata URL** | `https://<your-domain>/api/v1/sso/metadata?tenant=<tenant_id>` |
| **Single Logout URL** (optional) | `https://<your-domain>/api/v1/sso/logout?tenant=<tenant_id>` |

## Required SAML Attributes

Ensure your IdP sends these attributes in the SAML assertion:

| Attribute | Required | Description |
|-----------|----------|-------------|
| `NameID` | **Yes** | User's email address |
| `email` | Recommended | Explicit email attribute (fallback to NameID) |
| `displayName` | Recommended | User's full display name |
| `firstName` | Optional | First name (combined with lastName if displayName is absent) |
| `lastName` | Optional | Last name |

## Step 1: Configure SSO via Admin API

```bash
# Configure SAML settings for a tenant
curl -X PUT https://app.civiclens.ai/api/v1/admin/sso/config \
  -H "X-API-Key: <tenant-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "idp_entity_id": "https://your-idp.example.com/saml/metadata",
    "idp_sso_url": "https://your-idp.example.com/saml/sso",
    "idp_x509_cert": "MIIDpDCCAoy...<base64-encoded-certificate>",
    "sp_entity_id": "https://app.civiclens.ai/saml/your-tenant",
    "enabled": true,
    "idp_slo_url": "https://your-idp.example.com/saml/slo",
    "default_role": "viewer",
    "allowed_domains": "yourcity.gov,youragency.gov"
  }'
```

### Configuration Fields

| Field | Required | Description |
|-------|----------|-------------|
| `idp_entity_id` | **Yes** | Your IdP's entity ID (from IdP metadata) |
| `idp_sso_url` | **Yes** | IdP's Single Sign-On URL |
| `idp_x509_cert` | **Yes** | IdP's X.509 signing certificate (PEM format, without headers) |
| `sp_entity_id` | **Yes** | Unique identifier for this CivicLens tenant |
| `enabled` | No | Enable/disable SSO (default: `false`) |
| `idp_slo_url` | No | IdP's Single Logout URL |
| `default_role` | No | Role for new users: `admin`, `analyst`, or `viewer` (default: `viewer`) |
| `allowed_domains` | No | Comma-separated email domains allowed to login |

## Step 2: Download SP Metadata

Provide this URL to your IdP admin to auto-configure the SAML integration:

```
https://app.civiclens.ai/api/v1/sso/metadata?tenant=<tenant_id>
```

Or fetch it programmatically:

```bash
curl https://app.civiclens.ai/api/v1/sso/metadata?tenant=your-tenant
```

## Step 3: Test the Login Flow

Direct users to:

```
https://app.civiclens.ai/api/v1/sso/login?tenant=<tenant_id>
```

Or, if using tenant subdomains:

```
https://<tenant_id>.app.civiclens.ai/api/v1/sso/login
```

## IdP-Specific Guides

### Okta

1. In Okta Admin, go to **Applications > Create App Integration**
2. Select **SAML 2.0**
3. Configure:
   - **Single sign-on URL**: `https://app.civiclens.ai/api/v1/sso/acs`
   - **Audience URI (SP Entity ID)**: `https://app.civiclens.ai/saml/<tenant_id>`
   - **Name ID format**: EmailAddress
   - **Application username**: Email
4. Under **Attribute Statements**, add:
   - `email` -> `user.email`
   - `displayName` -> `user.displayName`
   - `firstName` -> `user.firstName`
   - `lastName` -> `user.lastName`
5. Copy the **IdP metadata** values (Entity ID, SSO URL, certificate) and use them in the CivicLens API configuration call above.

### Azure AD (Microsoft Entra ID)

1. In Azure Portal, go to **Enterprise Applications > New Application > Create your own application**
2. Select **Integrate any other application you don't find in the gallery (Non-gallery)**
3. Go to **Single sign-on > SAML**
4. Configure **Basic SAML Configuration**:
   - **Identifier (Entity ID)**: `https://app.civiclens.ai/saml/<tenant_id>`
   - **Reply URL (ACS URL)**: `https://app.civiclens.ai/api/v1/sso/acs`
   - **Logout URL**: `https://app.civiclens.ai/api/v1/sso/logout?tenant=<tenant_id>`
5. Under **Attributes & Claims**, ensure these are mapped:
   - `emailaddress` -> `user.mail`
   - `name` -> `user.displayname`
6. Download the **Certificate (Base64)** from the SAML Signing Certificate section.
7. Copy **Login URL** and **Azure AD Identifier** for the API configuration.

### Google Workspace

1. In Google Admin Console, go to **Apps > Web and mobile apps > Add App > Add custom SAML app**
2. Copy the **SSO URL**, **Entity ID**, and **Certificate** from the Google IdP information page.
3. Configure the **Service Provider Details**:
   - **ACS URL**: `https://app.civiclens.ai/api/v1/sso/acs`
   - **Entity ID**: `https://app.civiclens.ai/saml/<tenant_id>`
   - **Name ID format**: EMAIL
   - **Name ID**: Basic Information > Primary email
4. Add **Attribute mapping**:
   - `email` -> Basic Information > Primary email
   - `displayName` -> Basic Information > First name + Last name
5. Enable the app for your organizational unit.

## Role Mapping

CivicLens supports three roles with escalating permissions:

| Role | Permissions |
|------|-------------|
| **viewer** | Query the meeting archive (read-only) |
| **analyst** | Query + export data + configure alerts |
| **admin** | Full access + manage SSO settings + manage users |

New users are assigned the `default_role` configured in SSO settings (defaults to `viewer`). Browser-based SSO sessions must have the `admin` role to manage SSO configuration and users. Tenant API keys continue to act as full-tenant credentials. Admins can update roles via the API:

```bash
# List users
curl https://app.civiclens.ai/api/v1/admin/users \
  -H "X-API-Key: <tenant-api-key>"

# Update a user's role
curl -X PATCH https://app.civiclens.ai/api/v1/admin/users/<user_id>/role \
  -H "X-API-Key: <tenant-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"role": "analyst"}'
```

## Session Management

- JWT tokens expire after **8 hours**
- Users can explicitly log out via `/api/v1/sso/logout`
- If the IdP supports Single Logout (SLO), CivicLens will initiate federated logout
- The JWT is delivered both as a URL hash fragment (`#token=...`) and an httponly cookie (`civiclens_session`)

## Troubleshooting

### "SSO not configured for tenant"
The tenant doesn't have SSO settings yet. Use the PUT `/api/v1/admin/sso/config` endpoint to configure it.

### "SSO is disabled for tenant"
SSO is configured but `enabled` is `false`. Update the config with `"enabled": true`.

### "SAML validation failed"
The SAML assertion signature is invalid. Common causes:
- Wrong IdP certificate -- ensure you're using the correct X.509 signing certificate (not the encryption cert)
- Clock skew -- ensure the server clock is synchronized (NTP). SAML assertions have a time window.
- Wrong ACS URL -- ensure the ACS URL in your IdP matches exactly: `https://<your-domain>/api/v1/sso/acs`

### "Email domain not allowed"
The user's email domain is not in the `allowed_domains` list. Update the SSO config to include their domain, or remove domain restrictions.

### "JWT missing tenant_id claim" on API endpoints
The JWT was created before the SSO upgrade. Have the user log out and log in again.

### Enable debug logging
Set `SAML_DEBUG=true` in your environment to get detailed SAML protocol logging. Remember to disable it in production.

## API Reference

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/v1/sso/login` | GET | None | Redirect to IdP (pass `?tenant=` or use subdomain) |
| `/api/v1/sso/acs` | POST | None | SAML callback (IdP posts assertion here) |
| `/api/v1/sso/metadata` | GET | None | SP metadata XML for IdP configuration |
| `/api/v1/sso/logout` | GET | Optional JWT | Single logout |
| `/api/v1/auth/me` | GET | JWT | Get current authenticated user |
| `/api/v1/admin/sso/config` | PUT | Tenant API key or SSO admin session | Configure SSO settings |
| `/api/v1/admin/sso/config` | GET | Tenant API key or SSO admin session | Get SSO configuration |
| `/api/v1/admin/users` | GET | Tenant API key or SSO admin session | List tenant's SSO users |
| `/api/v1/admin/users/{id}/role` | PATCH | Tenant API key or SSO admin session | Update user role |
