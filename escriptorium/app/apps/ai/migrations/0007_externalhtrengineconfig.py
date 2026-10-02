from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('ai', '0006_azure_mistral_providers'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExternalHTREngineConfig',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=256, unique=True)),
                ('endpoint_url', models.URLField(help_text='Base URL of the engine process. Routes are not called from here.')),
                ('enabled', models.BooleanField(default=False, help_text='Transcription jobs do not read this flag.')),
                ('tier', models.CharField(choices=[('production', 'production'), ('api', 'api'), ('research', 'research')], max_length=16)),
                ('experimental', models.BooleanField(default=False)),
                ('timeout_seconds', models.PositiveIntegerField(default=30, validators=[django.core.validators.MinValueValidator(1)])),
                ('metadata', models.JSONField(blank=True, default=dict, help_text='Non-secret options. Do not store API keys.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'external HTR engine',
                'ordering': ['name'],
            },
        ),
    ]
