from random import choice
from time import sleep, time
from typing import Dict, List
from timeit import default_timer as timer
from config.schema import BELT_SCHEMA
from utilities.constants import ERROR_CODE, SUCCESS_CODE
from utilities.error_handling import (
    EmptySlotRequired,
    InvalidComponent,
    DuplicateComponent,
    InconsistentProduct,
)
from jsonschema import validate, ValidationError

from utilities.helpers import JSONFileHandler
from utilities.logger import Logger

log = Logger(__name__)


class Product:
    def __init__(self, items: List[str | None], components: List[str], finished_product: str):
        """
        A class that defined wha a product is made up of, how often it enters the belt, and what goes onto its slots.

        :param items: A list of singular alphanumerical characters e.g. ['A', 'B', '1', '2'].
        :param components: A list of all components and any other special or alphanumerical or empty characters.
        :param finished_product: A combination of all components mark a finished product
                 e.g. AB12, 1BA2, B2A1 => maps to a value on the belt to mark what the finished product should look like
        """
        self.items = items
        self.components = components
        self.finished_product = finished_product
        self.assembled_products = []

    def validate_empty_slot(self):
        if None not in self.items:
            raise EmptySlotRequired(f'An empty slot is required for releasing a finished product.')

    def validate_supplied_components(self):
        if not self.finished_product.isalnum():
            raise ValueError(f'Finished product "{self.finished_product}" is invalid.')

        for component in self.components:
            if component is None:
                raise InvalidComponent(f'Supplied an empty component(s).')
            if not component.isalnum():
                raise InvalidComponent(f'Component type "{component}" is invalid.')
            if len(component) != 1:
                raise InvalidComponent(f'Component "{component}" must be singular')
            if component == self.finished_product:
                raise InvalidComponent(f'Component "{component}" is wasting the workers\' time.')
            if component not in self.items:
                raise InvalidComponent(f'Component "{component}" is not recognized. Please check supplied items.')
            # Can alternatively use set() and ignore duplicates instead of raising an exception
            if len([comp for comp in self.components if comp == component]) != 1:
                raise DuplicateComponent(f'Component "{component}" is duplicate.')

    def validate(self):
        try:
            self.validate_supplied_components()
            self.validate_empty_slot()
        except InvalidComponent as e:
            log.error(e)
            exit(ERROR_CODE)
        except EmptySlotRequired as e:
            log.error(e)
            exit(ERROR_CODE)
        except DuplicateComponent as e:
            log.error(e)
            exit(ERROR_CODE)

    def get_item_randomly(self) -> str:
        return choice(self.items)


class ConveyorBelt:
    assembled_products_combination = []

    def __init__(self, belt_length: int, product: Product, belt_iterations: int, belt_delay: int):
        """
        The belt will pass components, other items, including empty slots.
        :param belt_length: The length of the belt i.e. number of slots to be created.
        :param product: A product object which defines a set of attributes that help provide instructions on how to
                        assemble it and the elements involved in doing so.
                        The use here is to tell the belt what should go on its slots.
        :param belt_delay: How fast do slots move in seconds
        :param belt_iterations: The number of belt iterations/steps
        """
        self.slots = [None] * belt_length
        self.belt_length = belt_length
        self.product = product
        self.belt_iterations = belt_iterations
        self.belt_delay = belt_delay
        self.unpicked_components_counter = self.generate_counter(self.product.components + ['other'])
        self.finished_products_counter = self.generate_counter(self.product.finished_product)

    def __str__(self):
        return f'Belt {self.slots} at iteration {self.belt_iterations}'

    @staticmethod
    def generate_counter(data: List[str] | str) -> Dict[str, int]:
        local_data = data
        if isinstance(data, str):
            local_data = [data]
        elif not isinstance(data, list):
            raise ValueError(f'Dataset "{data}" must either be string or list but was "{type(data)}".')

        return {
            item: 0 for item in local_data
        }

    def move_belt(self):
        last_item = self.slots[-1]

        if last_item == self.product.finished_product:
            self.finished_products_counter[last_item] += 1
        elif last_item in self.product.components:
            self.unpicked_components_counter[last_item] += 1
        else:
            self.unpicked_components_counter['other'] += 1

        # The belt can use sleep(N) but not for the worker during the assembly process because otherwise it will hold
        #   the belt from moving sleep(0) will cause unexpected behavior.
        # In Python, it may hint to the OS that the
        #   current thread is willing to giv up its time slot to allow other threads or processes to run.
        # It may cause race condition or concurrency issues and so on.
        if self.belt_delay > 0:
            sleep(self.belt_delay)
        self.slots = [self.product.get_item_randomly()] + self.slots[:-1]
        # Count each slot move when a worker is assembling
        #   otherwise they are not counted as part of the "steps/iterations" resulting in more iterations than specified
        self.belt_iterations -= 1
        log.info(self.__str__())

    def remove_component(self, slot_index: int):
        self.slots[slot_index] = None


class Worker:
    next_id = 1

    def __init__(self, belt: ConveyorBelt, product: Product, assembly_time: int = 3):
        """
        An intermediate product is a combination of components apart from the final missing component
        e.g. if the components are ['A', '1', '2', 'B'] then an intermediate product is AB1, B2, A21 but not A1B2
            as that's a finished product.
        The workers can place on the belt a finished products only.
        :param belt: The conveyor belt. The worker needs to know about the belt and product
        :param product: A product object which defines a set of attributes that help provide instructions on how to
                        assemble it and the elements involved in doing so.
                        The use here is to tell the worker what needs to be assembled and how to assemble it.
        :param assembly_time: The time it takes to assemble an intermediate product or a finished product
        """
        self.left_hand = None
        self.right_hand = None
        self.product = product
        self.belt = belt
        self.assembly_time = assembly_time
        self.worker_id = Worker.next_id
        self.holds_finished_product = False
        Worker.next_id += 1

    def __str__(self):
        return f'Worker ({self.worker_id}) hands ({self.left_hand} | {self.right_hand})'

    def pick_item(self, item: str) -> bool:
        if item == self.product.finished_product or item is None:
            return False
        if item not in self.product.components:
            return False
        if self.hands_occupied():
            return False

        if self.right_hand is None:
            # Since intermediate product and finished product are always assembled in the left hand
            #   the right hand should still be able to pick a component while the left has a finished product.
            if self.holds_finished_product:
                self.right_hand = item
            elif self.left_hand is None:
                self.right_hand = item
            elif item not in self.left_hand:
                self.right_hand = item
            else:
                # The left hand has either a component or an intermediate product
                #   and the `item` exists in the left hand.
                return False

        elif self.left_hand is None:
            if self.right_hand is None:
                self.left_hand = item
            elif item not in self.right_hand:
                self.left_hand = item
            else:
                # The right hand has the same component as `item`
                return False
        return True

    def assembled_finished_product(self) -> bool:
        if self.left_hand is None:
            return False

        if (
            not all(component in self.product.components for component in self.left_hand)
            or len(set(self.left_hand)) != len(self.left_hand)
        ):
            raise InconsistentProduct(f'Inconsistent product "{self.left_hand}" by worker "{self.worker_id}"')

        return len(self.left_hand) == len(self.product.components)

    def reset_left_hand(self) -> None:
        # If the right hand happened to have picked a component before the left hand had the opportunity to release
        #   a product then reset only the left hand once the product is released
        #   otherwise, the right hand would be None anyway.
        self.left_hand = None
        self.holds_finished_product = False

    def hands_occupied(self) -> bool:
        return (self.left_hand is not None) and (self.right_hand is not None)

    def assemble(self) -> str | None:
        if not self.hands_occupied() or self.holds_finished_product:
            return None

        self.left_hand += self.right_hand
        self.right_hand = None

        # Assembling an intermediate product or a finished product takes 3 seconds
        #   during which the belt should continue moving but no worker will interact with it.
        if self.assembly_time > 0:
            end_assembly = time() + self.assembly_time
    
            if self.belt.belt_delay == 0:
                log.warning('''
                    The belt is moving too fast.
                    It may finish running while a worker is assembling a product.
                    Consider slowing it down by >= 0.5 second.
                ''')

            log.debug(f'======> Moving the belt while worker ({self.worker_id}) is assembling...')
            while time() < end_assembly:
                self.belt.move_belt()
                # Special handling for when the belt moves during the assembly process
                #   as it's not captured by the outter while loop.
                if self.belt.belt_iterations == 0:
                    break

            actual_assembly_duration = time() - (end_assembly - self.assembly_time)
            log.debug(f'Actual assembly duration was {int(actual_assembly_duration)} seconds')

        return 'finished' if len(self.left_hand) == len(self.product.components) else 'intermediate'

    def release_finished_product(self, slot_index: int) -> bool:
        if not self.holds_finished_product or self.belt.slots[slot_index] is not None:
            return False

        self.belt.slots[slot_index] = self.product.finished_product
        ConveyorBelt.assembled_products_combination.extend([self.left_hand])
        return True


def run_simulation(belt: ConveyorBelt, workers: [Worker]) -> List[str]:
    """
    :param belt: A belt object
    :param workers: A list of N number of worker objects
    :return: A list of finished product combinations
    """
    while belt.belt_iterations > 0:
        belt.move_belt()

        for i in range(belt.belt_length):
            # Special handling for when the belt moves during the assembly process
            #   as it's not captured by the outter while loop.
            if belt.belt_iterations == 0:
                break

            workers_per_slot = workers[i]

            for worker in workers_per_slot:
                item = belt.slots[i]
                product_type = worker.assemble()

                if product_type is not None:
                    log.info(f'Worker ({worker.worker_id}) assembled {product_type} product ({worker.left_hand} | {worker.right_hand})')
                
                elif worker.release_finished_product(slot_index=i):
                    log.debug(f'Worker ({worker.worker_id}) released a product ({worker.left_hand}) => {worker.belt.slots}')
                    worker.reset_left_hand()
   
                elif worker.pick_item(item):
                    log.info(f'Worker ({worker.worker_id}) picked a component ({item})')
                    belt.remove_component(slot_index=i)

                else:
                    log.debug(f'Worker({worker.worker_id}) no action')

                # Update left hand status
                worker.holds_finished_product = worker.assembled_finished_product()
                log.info(str(worker))

        log.info('-' * 40)
    return ConveyorBelt.assembled_products_combination


if __name__ == '__main__':
    json_handler = JSONFileHandler()
    config = json_handler.read_json('config/config.json')
    try:
        validate(instance=config, schema=BELT_SCHEMA)
    except ValidationError as e:
        log.error(f'Invalid Belt Config \'{e.path[-1] if e.path else None}\': {e.message}')
        exit(ERROR_CODE)

    # Measure execution
    start = timer()

    _debug = config.get('debug', False)
    _belt_length = config['belt']['belt_length']
    _belt_iterations = config['belt']['belt_iterations']
    _belt_delay = config['belt']['belt_delay']
    _workers_per_slot = config['workers_per_slot']
    _finished_product = config['finished_product']
    _assembly_time = config['assembly_time']

    _assembled_products_combinations = []
    log.configure_logging(_debug)

    _product = Product(
        # ['A', 'B', 'C', 'D', 'E', 'F', 'AC', 'G', '1', '2', '3', '4', '5', None]
        items=['A', 'B', 'C', 'D', None],
        components=['A', 'B', 'C'],
        finished_product=_finished_product,
    )
    _product.validate()

    _belt = ConveyorBelt(_belt_length, _product, _belt_iterations, _belt_delay)
    _workers = [
        [Worker(_belt, _product, _assembly_time) for _ in range(_workers_per_slot)]
        for _ in range(_belt_length)
    ]

    try:
        _assembled_products_combinations = run_simulation(belt=_belt, workers=_workers)
    except InconsistentProduct as e:
        log.error(e)
        exit(ERROR_CODE)
    except KeyboardInterrupt:
        log.info('Exiting...')

    print(f'======== Execution time: {(timer() - start):.1f} seconds ========')
    log.info(f'The products combinations: {_assembled_products_combinations}')
    log.info(f'Unpicked components: {_belt.unpicked_components_counter}')
    log.info(f'Finished products: {_belt.finished_products_counter}')

    exit(SUCCESS_CODE)
