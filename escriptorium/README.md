This directory is the Escript AI Docker application stack.

Build and run Escript AI from this tree:

```bash
cp variables.env_example variables.env
# edit variables.env
docker compose build
docker compose up -d
```

Do not run the official `registry.gitlab.com/scripta/escriptorium` image as
the app. Escript AI now builds its own local images:

- `escript-ai:local` for Django, Celery, kraken, the Vue frontend, and the AI app
- `escript-ai-nginx:local` for nginx/static/media serving

The upstream eScriptorium image is older than this tree and does not include
the current kraken, API, localisation, or Phase 2 AI-layer review code.

## The stack
- nginx
- uWSGI
- [django](https://www.djangoproject.com/)
- [daphne](https://github.com/django/daphne) (channel server for websockets)
- [celery](http://www.celeryproject.org/)
- postgres
- redis (cache, celery broker, other disposable data)
- [kraken](http://kraken.re)
- [docker compose](https://docs.docker.com/compose/) (deployment)

Elasticsearch is optional and disabled by default.

## Runtime notes

- `entrypoint.sh` runs migrations and `collectstatic`.
- API keys belong in `variables.env` or another Docker env source, not in git.
- Remote AI providers are used only when a configured AI transcription job runs.
- Local OpenAI-compatible servers can be used without paid API calls.


## Upstream acknowledgement

Escript AI is independent work derived from eScriptorium. The material below is
kept as upstream acknowledgement and licence context, not as Escript AI install
instructions.

eScriptorium is part of the [Scripta](https://www.psl.eu/en/scripta), [RESILIENCE](https://www.resilience-ri.eu) and [Biblissima+](https://projet.biblissima.fr/) projects, and has received funding from Université PSL and from The European Union's [Horizon 2020 Research and Innovation Programme](https://ec.europa.eu/programmes/horizon2020/en/what-horizon-2020) under Grant Agreement no. 871127, from the Programme d'investissements d'avenir of the [Agence Nationale de Recheche](https://anr.fr/fr/france-2030/france-2030/) under Grant Reference no. ANR-21-ESRE-0005, as well as from other contributors listed below. Its goal is provide researchers in the humanities with an integrated set of tools to transcribe, annotate, translate and publish historical documents.

## Steering Committee

- Daniel Stoekl Ben Ezra (EPHE-PSL, UMR AOROC 8546)
- Peter Stokes (EPHE-PSL, UMR AOROC 8546)
- Benjamin Kiessling (EPHE-PSL, UMR AOROC 8546)
- Robin Tissot (EPHE-PSL, UMR AOROC 8546)
- Mathew Barber (Aga Khan University, Institute for the Study of Muslim Civilisations)
- David Smith (Northeastern University)
- Thibault Clérice (Inria)
- Hassen Aguili (Inria)

## Current financial and technical contributors include:
- [École Pratique des Hautes Études (EPHE)](https://www.ephe.psl.eu)
- [Biblissima+](https://projet.biblissima.fr/)
- [Resilience](https://www.resilience-ri.eu/)
- [PSL Scripta](https://scripta.psl.eu/en/)
- [Institut national de recherche en sciences et technologies du numérique (INRIA)](https://inria.fr/en)
- [Archives nationales de France](https://www.archives-nationales.culture.gouv.fr/)
- [L’Institut de recherche et d’histoire des textes](https://www.irht.cnrs.fr/)
- [Open Islamicate Texts Initiative (OpenITI)](https://openiti.org/)
- [The Andrew W. Mellon Foundation](https://mellon.org/grants/)
