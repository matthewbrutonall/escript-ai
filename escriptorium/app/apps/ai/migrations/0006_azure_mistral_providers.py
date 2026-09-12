from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ai', '0005_aiuserquota_aiuserkey'),
    ]

    operations = [
        migrations.AlterField(
            model_name='aibackendconfig',
            name='provider',
            field=models.CharField(
                choices=[
                    ('gemini', 'Google Gemini'),
                    ('anthropic', 'Anthropic Claude'),
                    ('openai', 'OpenAI'),
                    ('local', 'Local (OpenAI-compatible: Ollama/vLLM)'),
                    ('azure', 'Azure OpenAI'),
                    ('mistral', 'Mistral / Pixtral'),
                ],
                max_length=32,
            ),
        ),
    ]
