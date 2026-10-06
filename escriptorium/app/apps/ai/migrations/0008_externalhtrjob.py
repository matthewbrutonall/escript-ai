from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('ai', '0007_externalhtrengineconfig'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0076_alter_ocrmodel_file'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExternalHTRJob',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('model_id', models.CharField(blank=True, default='', help_text='Contract model id. Not a file path.', max_length=256)),
                ('status', models.CharField(choices=[('planned', 'planned'), ('sent', 'sent'), ('completed', 'completed'), ('failed', 'failed'), ('cancelled', 'cancelled')], default='planned', max_length=16)),
                ('layer_source', models.CharField(blank=True, help_text='version_source stamp. Empty until a response is planned.', max_length=128, null=True)),
                ('engine', models.CharField(blank=True, default='', max_length=256)),
                ('model_version', models.CharField(blank=True, default='', max_length=256)),
                ('api_version', models.CharField(blank=True, default='', max_length=16)),
                ('requested_line_count', models.PositiveIntegerField(default=0)),
                ('skipped_line_count', models.PositiveIntegerField(default=0)),
                ('result_count', models.PositiveIntegerField(default=0)),
                ('warning_count', models.PositiveIntegerField(default=0)),
                ('empty_text_count', models.PositiveIntegerField(default=0)),
                ('code', models.CharField(blank=True, default='', help_text='Fixed status code. Do not store an endpoint, a path, or a response body.', max_length=64)),
                ('message', models.CharField(blank=True, default='', help_text='Fixed status text. Do not store API keys, endpoints, document paths, or raw responses.', max_length=256)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='external_htr_jobs', to='core.document')),
                ('part', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='external_htr_jobs', to='core.documentpart')),
                ('config', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='jobs', to='ai.externalhtrengineconfig')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='external_htr_jobs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'external HTR job',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ExternalHTRLineResult',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('line_id', models.CharField(max_length=256)),
                ('position', models.PositiveIntegerField(default=0, help_text='Order of this line in the request.')),
                ('text', models.TextField(blank=True, default='', help_text='Recognized text. Empty text is valid. Do not store an image.')),
                ('confidence', models.FloatField(blank=True, null=True)),
                ('timing_ms', models.PositiveIntegerField(default=0)),
                ('warnings', models.JSONField(blank=True, default=list, help_text='Short warning strings. Do not store API keys or raw images.')),
                ('status', models.CharField(choices=[('included', 'included'), ('skipped', 'skipped'), ('failed', 'failed')], default='included', max_length=16)),
                ('code', models.CharField(blank=True, default='', help_text='Fixed line code, such as a skip reason.', max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='ai.externalhtrjob')),
            ],
            options={
                'verbose_name': 'external HTR line result',
                'ordering': ['job_id', 'position', 'pk'],
                'unique_together': {('job', 'line_id')},
            },
        ),
    ]
