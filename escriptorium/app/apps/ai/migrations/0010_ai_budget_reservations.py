import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ai', '0009_externalhtrjob_import_link'),
        ('core', '0086_seed_default_ontology'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='aijob',
            name='claim',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='aijob',
            name='heartbeat_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='AIBudgetLock',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scope', models.CharField(max_length=64, unique=True)),
            ],
        ),
        migrations.CreateModel(
            name='AIBudgetHold',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount_usd', models.DecimalField(decimal_places=6, max_digits=14)),
                ('state', models.CharField(choices=[('open', 'Open'), ('closed', 'Closed')], default='open', max_length=8)),
                ('close_reason', models.CharField(blank=True, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('job', models.OneToOneField(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='budget_hold', to='ai.aijob')),
                ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['state'], name='ai_aibudget_state_4f5c1e_idx')],
            },
        ),
        migrations.CreateModel(
            name='AIBillableCall',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(max_length=32)),
                ('model_id', models.CharField(max_length=128)),
                ('state', models.CharField(choices=[('reserved', 'Reserved (in flight)'), ('confirmed', 'Confirmed by provider usage'), ('estimated', 'Answered without usage (estimated)'), ('unknown', 'Billing outcome unknown'), ('released', 'Not billed')], default='reserved', max_length=16)),
                ('reserved_usd', models.DecimalField(decimal_places=6, max_digits=14)),
                ('confirmed_usd', models.DecimalField(blank=True, decimal_places=6, max_digits=14, null=True)),
                ('estimated_usd', models.DecimalField(blank=True, decimal_places=6, max_digits=14, null=True)),
                ('tokens_in', models.BigIntegerField(blank=True, null=True)),
                ('tokens_out', models.BigIntegerField(blank=True, null=True)),
                ('sent', models.BooleanField(default=False)),
                ('error', models.CharField(blank=True, help_text='Exception class and HTTP status only. No response body.', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('settled_at', models.DateTimeField(blank=True, null=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('resolution_note', models.TextField(blank=True)),
                ('document', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.document')),
                ('hold', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='calls', to='ai.aibudgethold')),
                ('job', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='billable_calls', to='ai.aijob')),
                ('ledger', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='billable_call', to='ai.aiusageledger')),
                ('resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['state', 'created_at'], name='ai_aibillab_state_9a2d4b_idx'),
                    models.Index(fields=['user', 'created_at'], name='ai_aibillab_user_id_1c7e3f_idx'),
                ],
            },
        ),
    ]
