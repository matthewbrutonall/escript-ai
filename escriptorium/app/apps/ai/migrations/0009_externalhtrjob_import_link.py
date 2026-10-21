from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('ai', '0008_externalhtrjob'),
        ('core', '0086_seed_default_ontology'),
    ]

    operations = [
        migrations.AddField(
            model_name='externalhtrjob',
            name='imported',
            field=models.BooleanField(
                default=False,
                help_text='True after a transcription layer has been created from this audit. Stays true if that layer is later removed.',
            ),
        ),
        migrations.AddField(
            model_name='externalhtrjob',
            name='imported_transcription',
            field=models.OneToOneField(
                blank=True,
                help_text='Layer created from this audit. Empty until import succeeds.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='external_htr_import',
                to='core.transcription',
            ),
        ),
    ]
