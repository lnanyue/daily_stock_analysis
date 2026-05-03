# Configuration Migration Guide

## Overview

The configuration system has been simplified from a four-layer structure (.env + settings.yaml + litellm_config.yaml + config_registry.py) to a two-file structure:

- `.env` — Sensitive keys and tokens only
- `config.yaml` — All business parameters and LLM routing

## Migration Steps

### Step 1: Backup Your Current Configuration

```bash
cp .env .env.backup
cp settings.yaml settings.yaml.backup
cp litellm_config.yaml litellm_config.yaml.backup
```

### Step 2: Update .env File

Remove all business parameters from `.env`:

**Remove these types of parameters:**
- `MAX_WORKERS`, `LOG_LEVEL`, `DEBUG`
- `SCHEDULE_ENABLED`, `SCHEDULE_TIME`, `MARKET_REVIEW_ENABLED`
- `REPORT_TYPE`, `REPORT_LANGUAGE`, `REPORT_SUMMARY_ONLY`
- `BIAS_THRESHOLD`, `NEWS_MAX_AGE_DAYS`, `ANALYSIS_MODE`
- All `LLM_*` and `LITELLM_*` variables (move to config.yaml llm section)

**Keep in .env:**
- All API keys and tokens (GEMINI_API_KEY, DEEPSEEK_API_KEY, etc.)
- All notification webhook URLs and credentials
- All data source credentials (TUSHARE_TOKEN, FUTU_*, etc.)

### Step 3: Create config.yaml

Copy the `config.yaml` template from the repository root into your working directory, then customize:

```bash
# The template is already in the repo root
# Edit it with your preferred values
vim config.yaml
```

### Step 4: Migrate settings.yaml Parameters

If you had custom values in `settings.yaml`, move them to `config.yaml`:

| settings.yaml path | config.yaml path |
|-------------------|-------------------|
| `analysis.mode` | `analysis.mode` |
| `analysis.language` | `analysis.language` |
| `analysis.bias_threshold` | `analysis.bias_threshold` |
| `system.max_workers` | `system.max_workers` |
| `system.log_level` | `system.log_level` |
| `system.report_dir` | `system.report_dir` |
| `notification.report_type` | `notification.report_type` |
| `notification.summary_only` | `notification.summary_only` |
| `data.prefetch_quotes` | `data.prefetch_quotes` |
| `data.cache_ttl` | `data.cache_ttl` |

### Step 5: Migrate litellm_config.yaml Parameters

If you used `litellm_config.yaml`, move the configuration to `config.yaml`:

```yaml
# Old litellm_config.yaml
model_list:
  - model_name: deepseek-v4-flash
    litellm_params:
      model: deepseek/deepseek-v4-flash
      api_key: "os.environ/DEEPSEEK_API_KEY"

# New config.yaml
llm:
  primary_model: "deepseek/deepseek-v4-flash"
  fallback_models: []
  temperature: 0.7
  channels: []
```

Or set `LITELLM_CONFIG` in `.env` to point to your existing YAML file.

### Step 6: Validate Configuration

```bash
python -c "from src.config import get_config; get_config()"
```

If validation fails, check the error messages and fix the configuration.

### Step 7: Remove Old Files (Optional)

After confirming everything works:

```bash
rm settings.yaml.backup
rm litellm_config.yaml.backup
# Keep .backup files until you're sure the migration is successful
```

## FAQ

**Q: What if I still have business parameters in .env?**
A: The new `UnifiedConfigLoader` will still read them, but a deprecation warning will be logged. Please migrate to `config.yaml`.

**Q: Can I keep using settings.yaml?**
A: It's marked as deprecated. The new loader doesn't read it. Please migrate to `config.yaml`.

**Q: Where did my LLM routing config go?**
A: Move it to the `llm` section in `config.yaml`, or set `LITELLM_CONFIG` env var.

**Q: Validation fails with "required field missing"**
A: Check that you have at least one LLM API key set in `.env` (e.g., `GEMINI_API_KEY`).
