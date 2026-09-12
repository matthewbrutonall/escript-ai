from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0076_alter_ocrmodel_file'),
        ('ai', '0002_phase2_gate'),
    ]

    operations = [
        migrations.AddField(
            model_name='ailayergate',
            name='held_out_part_pks',
            field=models.JSONField(
                blank=True, default=list,
                help_text='Reviewed pages held out of kraken training for ketos test.'),
        ),
        migrations.AddField(
            model_name='ailayergate',
            name='held_out_cer',
            field=models.FloatField(
                blank=True, help_text='Optional ketos test CER on held-out reviewed pages.',
                null=True),
        ),
        migrations.CreateModel(
            name='AIExample',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.CharField(max_length=2048)),
                ('pinned', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_examples', to='core.document')),
                ('line', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.line')),
            ],
            options={
                'ordering': ['-pinned', '-updated_at'],
                'unique_together': {('document', 'line')},
            },
        ),
    ]
