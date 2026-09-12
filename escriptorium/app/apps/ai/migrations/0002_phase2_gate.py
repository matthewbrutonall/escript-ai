from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0076_alter_ocrmodel_file'),
        ('ai', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='aibackendconfig',
            name='conventions',
            field=models.JSONField(
                blank=True, default=dict,
                help_text='Diplomatic toggles (abbreviations, long-s, u/v, hyphenation). Empty uses defaults: keep original spelling, do not expand.'),
        ),
        migrations.CreateModel(
            name='AILayerGate',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('state', models.CharField(choices=[('raw', 'Raw'), ('sampled', 'Sampled'), ('training-eligible', 'Training-eligible')], default='raw', max_length=32)),
                ('sample_line_pks', models.JSONField(blank=True, default=list)),
                ('mean_cer', models.FloatField(blank=True, null=True)),
                ('acknowledged_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('acknowledged_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('comparison', models.ForeignKey(blank=True, help_text='Kraken (or other) layer used for disagreement CER.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ai_gate_comparisons', to='core.transcription')),
                ('job', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='layer_gates', to='ai.aijob')),
                ('transcription', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='ai_gate', to='core.transcription')),
            ],
        ),
        migrations.CreateModel(
            name='AILineDisagreement',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ai_text', models.TextField(blank=True)),
                ('comparison_text', models.TextField(blank=True)),
                ('cer', models.FloatField()),
                ('in_sample', models.BooleanField(default=False)),
                ('gate', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='disagreements', to='ai.ailayergate')),
                ('line', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.line')),
            ],
            options={
                'ordering': ['-cer'],
                'unique_together': {('gate', 'line')},
            },
        ),
    ]
