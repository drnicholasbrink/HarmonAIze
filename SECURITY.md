# Security Policy

HarmonAIze processes sensitive research health data. Please report security problems privately so they can be fixed before details are public.

## Reporting a vulnerability

**Do not open a public GitHub issue, discussion or pull request for a security problem.**

Report it through GitHub's private vulnerability reporting:

1. Go to the repository's [Security tab](https://github.com/drnicholasbrink/HarmonAIze/security).
2. Choose **Report a vulnerability**.
3. Describe the problem, the affected file or URL, how to reproduce it, and the impact.

Only the maintainers can see the report. We aim to acknowledge reports within five working days and will agree a disclosure timeline with you.

If you find credentials, API keys or real participant data in the repository or its history, report them the same way. Do not test whether they work.

## Scope

In scope: the HarmonAIze Django application in `harmonaize/`, its Docker configuration, and its integration with the vendored Armadillo service.

Out of scope: vulnerabilities in third-party services themselves (OpenAI, Google Earth Engine, Mapbox, Armadillo upstream). Report those to the vendor.

## Supported versions

Only the latest `main` branch receives security fixes.
