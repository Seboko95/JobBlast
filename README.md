# JobBlast

![JobBlast Pipeline Arrow logo](assets/brand/jobblast-pipeline-arrow-selected.png)

JobBlast is a job-search and application-support project with Python services, a Telegram interface, an Expo mobile client, and a web application.

## Repository contents

- Python API, bot, job-source, application-support, and storage modules
- Expo mobile client and web application source
- Tests and dependency manifests
- The selected Pipeline Arrow brand asset

## Local data and credentials

This public source snapshot excludes user resumes, uploaded documents, databases, logs, generated output, and local environment files. Copy `.env.example` to `.env` for local setup and supply credentials only on your own machine. Do not commit `.env`, user application data, or runtime logs.

Review generated application materials and any automated actions before use. Configure access controls deliberately before connecting real accounts or external services.

## Development

Use Python 3 and the dependency versions in `requirements.txt`. The `mobile` and `website` folders have separate package manifests; install their dependencies locally with `npm ci` when working on those clients. Run only the project-specific commands you intend to use.

No open-source license is included in this snapshot.

