from django.db import transaction
from django.db.models import Prefetch
from django.db.models.query_utils import Q
from django_filters import rest_framework as filters
from dry_rest_permissions.generics import DRYPermissionFiltersBase, DRYPermissions
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from care.facility.api.serializers.patient_sample import (
    PatientSamplePatchSerializer,
    PatientSampleReadSerializer,
    PatientSampleSerializer,
)
from care.facility.models import PatientSample, PatientSampleFlow, User, patient_data


class PatientSampleFilterBackend(DRYPermissionFiltersBase):
    def filter_queryset(self, request, queryset, view):
        if request.user.is_superuser:
            pass
        else:
            q_objects = Q(consultation__facility__created_by=request.user)
            if request.user.user_type >= User.TYPE_VALUE_MAP["StateLabAdmin"]:
                q_objects |= Q(consultation__facility__state=request.user.state)
            elif request.user.user_type >= User.TYPE_VALUE_MAP["DistrictLabAdmin"]:
                q_objects |= Q(consultation__facility__district=request.user.district)
            queryset = queryset.filter(q_objects)
        return queryset


class PatientSampleFilterSet(filters.FilterSet):
    district = filters.NumberFilter(field_name="consultation__facility__district_id")
    district_name = filters.CharFilter(field_name="consultation__facility__district__name", lookup_expr="icontains")
    status = filters.ChoiceFilter(choices=PatientSample.SAMPLE_TEST_FLOW_CHOICES)
    result = filters.ChoiceFilter(choices=PatientSample.SAMPLE_TEST_RESULT_CHOICES)


class PatientSampleViewSet(viewsets.ModelViewSet):
    serializer_class = PatientSampleSerializer
    queryset = (
        PatientSample.objects.all()
        .prefetch_related(
            Prefetch(
                "patientsampleflow_set",
                PatientSampleFlow.objects.all().order_by("-created_date"),
                to_attr="flow_prefetched",
            )
        )
        .order_by("-id")
    )
    permission_classes = (
        IsAuthenticated,
        DRYPermissions,
    )
    filter_backends = (
        PatientSampleFilterBackend,
        filters.DjangoFilterBackend,
    )
    filterset_class = PatientSampleFilterSet
    http_method_names = ["get", "post", "patch", "delete"]

    def get_serializer_class(self):
        serializer_class = self.serializer_class
        if self.request.method == "GET":
            serializer_class = PatientSampleReadSerializer
        elif self.request.method == "PATCH":
            serializer_class = PatientSamplePatchSerializer
        return serializer_class

    def get_queryset(self):
        queryset = super(PatientSampleViewSet, self).get_queryset()
        if self.kwargs.get("patient_pk") is not None:
            queryset = queryset.filter(patient_id=self.kwargs.get("patient_pk"))
        return queryset

    def list(self, request, *args, **kwargs):
        """
        Patient Sample List

        Available Filters
        - district - District ID
        - district_name - District name - case insensitive match
        """
        return super(PatientSampleViewSet, self).list(request, *args, **kwargs)

    def perform_create(self, serializer):
        validated_data = serializer.validated_data
        if self.kwargs.get("patient_pk") is not None:
            validated_data["patient_id"] = self.kwargs.get("patient_pk")
        notes = validated_data.pop("notes", "create")
        if not validated_data.get("patient_id") and not validated_data.get("consultation_id"):
            raise ValidationError({"non_field_errors": ["Either of patient_id or consultation_id is required"]})
        if "consultation_id" not in validated_data:
            try:
                validated_data["consultation"] = patient_data.PatientConsultation.objects.filter(
                    patient=validated_data["patient_id"]
                ).order_by("-id")[0]
            except IndexError:
                raise ValidationError({"patient_id": ["Invalid id/ No consultation done"]})
        else:
            try:
                validated_data["consultation"] = patient_data.PatientConsultation.objects.get(
                    id=validated_data["consultation_id"]
                )
            except patient_data.PatientConsultation.DoesNotExist:
                raise ValidationError({"consultation_id": ["Invalid id"]})

        with transaction.atomic():
            instance = serializer.create(validated_data)
            instance.patientsampleflow_set.create(status=instance.status, notes=notes, created_by=self.request.user)
            return instance

    def perform_update(self, serializer):
        validated_data = serializer.validated_data
        notes = validated_data.pop("notes", f"updated by {self.request.user.get_username()}")
        with transaction.atomic():
            instance = serializer.update(serializer.instance, validated_data)
            instance.patientsampleflow_set.create(status=instance.status, notes=notes, created_by=self.request.user)
            return instance
