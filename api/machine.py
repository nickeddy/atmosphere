"""
Atmosphere service machine rest api.

"""
from core.models import AtmosphereUser as User
from django.core.paginator import Paginator,\
    PageNotAnInteger, EmptyPage
from django.db.models import Q

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from threepio import logger

from authentication.decorators import api_auth_token_required

from core.models.identity import Identity

from core.models.machine import compare_core_machines, filter_core_machine,\
    convert_esh_machine, ProviderMachine
    
from core.metadata import update_machine_metadata

from service.machine_search import search, CoreSearchProvider

from api import prepare_driver, failureJSON
from api.permissions import InMaintenance
from api.serializers import ProviderMachineSerializer,\
    PaginatedProviderMachineSerializer


def provider_filtered_machines(request, provider_id, identity_id):
    """
    Return all filtered machines. Uses the most common,
    default filtering method.
    """
    esh_driver = prepare_driver(request, identity_id)
    esh_machine_list = esh_driver.list_machines()
    logger.info("Total machines from esh:%s" % len(esh_machine_list))
    esh_machine_list = esh_driver.filter_machines(
        esh_machine_list,
        black_list=['eki-', 'eri-'])
    core_machine_list = [convert_esh_machine(esh_driver, mach, provider_id)
                           for mach in esh_machine_list]
    filtered_machine_list = filter(filter_core_machine, core_machine_list)
    sorted_machine_list = sorted(filtered_machine_list,
                                 cmp=compare_core_machines)
    return sorted_machine_list


def all_filtered_machines():
    return ProviderMachine.objects.exclude(
        Q(identifier__startswith="eki-")
        | Q(identifier__startswith="eri")).order_by("-application__start_date")


class MachineList(APIView):
    """
    Represents:
        A Manager of Machine
        Calls to the Machine Class
    TODO: POST when we have programmatic image creation/snapshots
    """

    permission_classes = (InMaintenance,)

    @api_auth_token_required
    def get(self, request, provider_id, identity_id):
        """
        Using provider and identity, getlist of machines
        TODO: Cache this request
        """
        filtered_machine_list = provider_filtered_machines(request,
                                                           provider_id,
                                                           identity_id)
        serialized_data = ProviderMachineSerializer(filtered_machine_list,
                                                    many=True).data
        response = Response(serialized_data)
        return response


class MachineHistory(APIView):
    """
    A MachineHistory provides machine history for an identity.

    GET - A chronologically ordered list of ProviderMachines for the identity.
    """

    permission_classes = (InMaintenance,)

    @api_auth_token_required
    def get(self, request, provider_id, identity_id):
        data = request.DATA
        user = User.objects.filter(username=request.user)

        if user and len(user) > 0:
            user = user[0]
        else:
            errorObj = failureJSON([{
                'code': 401,
                'message': 'User not found'}])
            return Response(errorObj, status=status.HTTP_401_UNAUTHORIZED)

        esh_driver = prepare_driver(request, identity_id)

        # Historic Machines
        all_machines_list = all_filtered_machines()

        if all_machines_list:
            history_machine_list =\
                [m for m in all_machines_list if
                 m.application.created_by.username == user.username]
            logger.warn(len(history_machine_list))
        else:
            history_machine_list = []

        page = request.QUERY_PARAMS.get('page')
        if page:
            paginator = Paginator(history_machine_list, 5)
            try:
                history_machine_page = paginator.page(page)
            except PageNotAnInteger:
                # If page is not an integer, deliver first page.
                history_machine_page = paginator.page(1)
            except EmptyPage:
                # Page is out of range.
                # deliver last page of results.
                history_machine_page = paginator.page(paginator.num_pages)
            serialized_data = \
                PaginatedProviderMachineSerializer(
                    history_machine_page).data
        else:
            serialized_data = ProviderMachineSerializer(
                history_machine_list).data

        response = Response(serialized_data)
        response['Cache-Control'] = 'no-cache'
        return response

def get_first(coll):
    """
    Return the first element of a collection, otherwise return False.
    """
    if coll and len(coll) > 0:
        return coll[0]
    else:
        return False


class MachineSearch(APIView):
    """
    A MachineHistory provides machine history for an identity.

    GET - A chronologically ordered list of ProviderMachines for the identity.
    """

    permission_classes = (InMaintenance,)
    
    @api_auth_token_required
    def get(self, request, provider_id, identity_id):
        """
        """
        data = request.DATA

        user = get_first(User.objects.filter(username=request.user))
        if not user:
            errorObj = failureJSON([{
                'code': 401,
                'message': 'User not found'}])
            return Response(errorObj, status=status.HTTP_401_UNAUTHORIZED)

        query = request.QUERY_PARAMS.get('query')
        if not query:
            errorObj = failureJSON([{
                'code': 400,
                'message': 'Query not provided'}])
            return Response(errorObj, status=status.HTTP_400_BAD_REQUEST)

        identity = get_first(Identity.objects.filter(id=identity_id))
        if not identity:
            errorObj = failureJSON([{
                'code': 400,
                'message': 'Identity not provided'}])
            return Response(errorObj, status=status.HTTP_400_BAD_REQUEST)
        
        search_result = search([CoreSearchProvider], identity, query)

        page = request.QUERY_PARAMS.get('page')
        if page:
            paginator = Paginator(search_result, 20)
            try:
                search_page = paginator.page(page)
            except PageNotAnInteger:
                # If page is not an integer, deliver first page.
                search_page = paginator.page(1)
            except EmptyPage:
                # Page is out of range.
                # deliver last page of results.
                search_page = paginator.page(paginator.num_pages)
            serialized_data = \
                PaginatedProviderMachineSerializer(
                    search_page).data
        else:
            serialized_data = ProviderMachineSerializer(
                search_result).data

        response = Response(serialized_data)
        response['Cache-Control'] = 'no-cache'
        return response


class Machine(APIView):
    """
    Represents:
        Calls to modify the single machine
    TODO: DELETE when we allow owners to 'end-date' their machine..
    """

    @api_auth_token_required
    def get(self, request, provider_id, identity_id, machine_id):
        """
        Lookup the machine information
        (Lookup using the given provider/identity)
        Update on server (If applicable)
        """
        esh_driver = prepare_driver(request, identity_id)
        eshMachine = esh_driver.get_machine(machine_id)
        coreMachine = convert_esh_machine(esh_driver, eshMachine, provider_id)
        serialized_data = ProviderMachineSerializer(coreMachine).data
        response = Response(serialized_data)
        return response

    @api_auth_token_required
    def patch(self, request, provider_id, identity_id, machine_id):
        """
        TODO: Determine who is allowed to edit machines besides
            coreMachine.owner
        """
        user = request.user
        data = request.DATA
        esh_driver = prepare_driver(request, identity_id)
        esh_machine = esh_driver.get_machine(machine_id)
        coreMachine = convert_esh_machine(esh_driver, esh_machine, provider_id)

        if not user.is_staff and user is not coreMachine.application.created_by:
            logger.warn('%s is Non-staff/non-owner trying to update a machine'
                        % (user.username))
            errorObj = failureJSON([{
                'code': 401,
                'message':
                'Only Staff and the machine Owner '
                + 'are allowed to change machine info.'}])
            return Response(errorObj, status=status.HTTP_401_UNAUTHORIZED)

        coreMachine.application.update(request.DATA)
        serializer = ProviderMachineSerializer(coreMachine,
                                               data=data, partial=True)
        if serializer.is_valid():
            logger.info('metadata = %s' % data)
            update_machine_metadata(esh_driver, esh_machine, data)
            serializer.save()
            logger.info(serializer.data)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @api_auth_token_required
    def put(self, request, provider_id, identity_id, machine_id):
        """
        TODO: Determine who is allowed to edit machines besides
            coreMachine.owner
        """
        user = request.user
        data = request.DATA
        esh_driver = prepare_driver(request, identity_id)
        esh_machine = esh_driver.get_machine(machine_id)
        coreMachine = convert_esh_machine(esh_driver, esh_machine, provider_id)

        if not user.is_staff and user is not coreMachine.application.created_by:
            logger.warn('Non-staff/non-owner trying to update a machine')
            errorObj = failureJSON([{
                'code': 401,
                'message':
                'Only Staff and the machine Owner '
                + 'are allowed to change machine info.'}])
            return Response(errorObj, status=status.HTTP_401_UNAUTHORIZED)
        coreMachine.application.update(data)
        serializer = ProviderMachineSerializer(coreMachine,
                                               data=data, partial=True)
        if serializer.is_valid():
            logger.info('metadata = %s' % data)
            update_machine_metadata(esh_driver, esh_machine, data)
            serializer.save()
            logger.info(serializer.data)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
