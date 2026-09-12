from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0076_alter_ocrmodel_file'),
        ('ai', '0003_phase2_fewshot_ketos'),
    ]

    operations = [
        migrations.AlterField(
            model_name='aijob',
            name='mode',
            field=models.CharField(
                choices=[
                    ('region', 'Keyed region'),
                    ('line', 'Per-line'),
                    ('seg_review', 'Segmentation review'),
                ],
                default='region',
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name='AISegSuggestion',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[
                    ('spurious', 'Spurious line'),
                    ('missed', 'Missed line'),
                    ('order', 'Reading order'),
                    ('typology', 'Line type'),
                ], max_length=16)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[
                    ('pending', 'Pending'),
                    ('accepted', 'Accepted'),
                    ('dismissed', 'Dismissed'),
                ], default='pending', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_seg_suggestions', to='core.document')),
                ('job', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='seg_suggestions', to='ai.aijob')),
                ('line', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ai_seg_suggestions', to='core.line')),
                ('part', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_seg_suggestions', to='core.documentpart')),
            ],
            options={
                'ordering': ['part_id', 'kind', 'pk'],
            },
        ),
    ]
