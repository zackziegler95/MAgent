"""
Train agents to gather food
"""

import argparse
import logging as log
import time
import random

import magent
#from magent.builtin.mx_model import DeepQNetwork as RLModel
from magent.builtin.tf_model import DeepQNetwork as RLModel
# change this line to magent.builtin.tf_model to use tensorflow


def load_config(size, mm_mode, pheromone_mode, pheromone_decay):
    gw = magent.gridworld
    cfg = gw.Config()

    cfg.set({"map_width": size, "map_height": size})
    cfg.set({"minimap_mode": mm_mode})
    cfg.set({"pheromone_mode": pheromone_mode}) # Can agents see pheromones? (always of its own group)
    cfg.set({"pheromone_decay": pheromone_decay}) # 0.05
 
    agent = cfg.register_agent_type(
        name="agent",
        attr={'width': 1, 'length': 1, 'hp': 1, 'speed': 3,
              'view_range': gw.CircleRange(7), 'attack_range': gw.CircleRange(1),
              'damage': 1, 'step_recover': 0,
              'step_reward': -0.01,  'dead_penalty': -1, 'attack_penalty': -0.1,
              'attack_in_group': 0, 'can_lay_pheromone': int(pheromone_mode)}) # Whether or not a group lays pheromones

    food = cfg.register_agent_type(
        name='food',
        attr={'width': 1, 'length': 1, 'hp': 50, 'speed': 0,
              'view_range': gw.CircleRange(1), 'attack_range': gw.CircleRange(0),
              'kill_reward': 5})

    g_f = cfg.add_group(food)
    g_s = cfg.add_group(agent)

    a = gw.AgentSymbol(g_s, index='any')
    b = gw.AgentSymbol(g_f, index='any')

    cfg.add_reward_rule(gw.Event(a, 'attack', b), receiver=a, value=0.5)
    
    return cfg


def generate_map(env, map_size, food_handle, handles, random_placement=False):
    center_x, center_y = map_size // 2, map_size // 2

    def add_square(pos, side, gap, offset_x=0, offset_y=0):
        bx = center_x + offset_x
        by = center_y + offset_y
        side = int(side)
        for x in range(bx - side//2, bx + side//2 + 1, gap):
            pos.append([x, by - side//2])
            pos.append([x, by + side//2])
        for y in range(by - side//2, by + side//2 + 1, gap):
            pos.append([bx - side//2, y])
            pos.append([bx + side//2, y])

    # agent
    pos = []
    add_square(pos, 4, 2)
    env.add_agents(handles[0], method="custom", pos=pos)

    # food
    pos = []
    
    offset_x = 15
    offset_y = 15
    if random_placement:
        if random.random() < 0.5:
            offset_x *= -1
        if random.random() < 0.5:
            offset_y *= -1

    add_square(pos, 4, 2, offset_x=offset_x, offset_y=offset_y)
    env.add_agents(food_handle, method="custom", pos=pos)


def play_a_round(env, map_size, food_handle, handles, models, train_id=-1,
                 print_every=10, record=False, render=False, eps=None,
                 random_placement=False):
    env.reset()
    generate_map(env, map_size, food_handle, handles, random_placement=random_placement)

    step_ct = 0
    total_reward = 0
    done = False

    pos_reward_ct = set()

    n = len(handles)
    obs  = [None for _ in range(n)]
    ids  = [None for _ in range(n)]
    acts = [None for _ in range(n)]
    nums = [env.get_num(handle) for handle in handles]
    sample_buffer = magent.utility.EpisodesBuffer(capacity=5000)

    print("===== sample =====")
    print("eps %s number %s" % (eps, nums))
    start_time = time.time()
    while not done:
        # take actions for every model
        for i in range(n):
            obs[i] = env.get_observation(handles[i])
            #print('------------------------------')
            #print(obs[i][0][0, :, :, 3])
            #print(obs[i][0][0, :, :, 4])
            ids[i] = env.get_agent_id(handles[i])
            acts[i] = models[i].infer_action(obs[i], ids[i], policy='e_greedy', eps=eps)
            env.set_action(handles[i], acts[i])

        # simulate one step
        done = env.step()

        # sample
        rewards = env.get_reward(handles[train_id])
        step_reward = 0
        if train_id != -1:
            alives  = env.get_alive(handles[train_id])
            total_reward += sum(rewards)
            sample_buffer.record_step(ids[train_id], obs[train_id], acts[train_id], rewards, alives)
            step_reward = sum(rewards)

        # render
        if render:
            env.render()

        for id, r in zip(ids[0], rewards):
            if r > 0.05 and id not in pos_reward_ct:
                pos_reward_ct.add(id)

        # clear dead agents
        env.clear_dead()

        # stats info
        for i in range(n):
            nums[i] = env.get_num(handles[i])
        food_num = env.get_num(food_handle)

        if step_ct % print_every == 0:
            print("step %3d,  train %d,  num %s,  reward %.2f,  total_reward: %.2f, non_zero: %d" %
                  (step_ct, train_id, [food_num] + nums, step_reward, total_reward, len(pos_reward_ct)))
        step_ct += 1

        if step_ct > 350:
            break

    sample_time = time.time() - start_time
    print("steps: %d,  total time: %.2f,  step average %.2f" % (step_ct, sample_time, sample_time / step_ct))

    if record:
        with open("reward-hunger.txt", "a") as fout:
            fout.write(str(nums[0]) + "\n")

    # train
    total_loss = value = 0
    if train_id != -1:
        print("===== train =====")
        start_time = time.time()
        total_loss, value = models[train_id].train(sample_buffer, print_every=250)
        train_time = time.time() - start_time
        print("train_time %.2f" % train_time)

    return total_loss, total_reward, value, len(pos_reward_ct)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--save_every", type=int, default=2)
    parser.add_argument("--render_every", type=int, default=10)
    parser.add_argument("--n_round", type=int, default=1500)
    parser.add_argument("--render", action='store_true')
    parser.add_argument("--load_from", type=int)
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--print_every", type=int, default=100)
    parser.add_argument("--map_size", type=int, default=40)
    parser.add_argument("--greedy", action="store_true")
    parser.add_argument("--name", type=str, default="gather2")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--eval", action="store_true")
    parser.add_argument("--random_placement", action="store_true")
    parser.add_argument("--mm_mode", action="store_true")
    parser.add_argument("--pheromone_mode", action="store_true")
    parser.add_argument("--pheromone_decay", type=float, default=0.05)
    args = parser.parse_args()

    # set logger
    log.basicConfig(level=log.INFO, filename=args.name + '.log')
    console = log.StreamHandler()
    console.setLevel(log.INFO)
    log.getLogger('').addHandler(console)

    # init env
    env = magent.GridWorld(load_config(args.map_size, args.mm_mode, args.pheromone_mode, args.pheromone_decay))
    env.set_render_dir("build/render/"+args.name)
    env.set_seed(123)

    handles = env.get_handles()
    food_handle = handles[0]
    player_handles = handles[1:]

    # sample eval observation set
    eval_obs = None
    if args.eval:
        print("sample eval set...")
        env.reset()
        generate_map(env, args.map_size, food_handle, player_handles, random_placement=args.random_placement)
        eval_obs = magent.utility.sample_observation(env, player_handles, 0, 2048, 500)

    # load models
    models = [
        RLModel(env, player_handles[0], args.name,
                batch_size=512, memory_size=2 ** 19, target_update=1000,
                train_freq=4, eval_obs=eval_obs)
    ]

    # load saved model
    save_dir = "save_model"
    if args.load_from is not None:
        start_from = args.load_from
        print("load models...")
        for model in models:
            model.load(save_dir, start_from)
    else:
        start_from = 0

    # print debug info
    print(args)
    print('view_space', env.get_view_space(player_handles[0]))
    print('feature_space', env.get_feature_space(player_handles[0]))
    print('view2attack', env.get_view2attack(player_handles[0]))

    if args.record:
        for k in range(4, 999 + 5, 5):
            eps = 0
            for model in models:
                model.load(save_dir, start_from)
                play_a_round(env, args.map_size, food_handle, player_handles, models,
                             -1, record=True, render=False,
                             print_every=args.print_every, eps=eps,
                             random_placement=args.random_placement)
    else:
        # play
        start = time.time()
        train_id = 0 if args.train else -1
        for k in range(start_from, start_from + args.n_round):
            tic = time.time()
            eps = magent.utility.piecewise_decay(k, [0, 10000, 30000, 60000], [0.9, 0.4, 0.2, 0.05]) if not args.greedy else 0
            loss, reward, value, pos_reward_ct = \
                    play_a_round(env, args.map_size, food_handle, player_handles, models,
                                 train_id, record=False,
                                 render=args.render or (k+1) % args.render_every == 0,
                                 print_every=args.print_every, eps=eps,
                                 random_placement=args.random_placement)
            log.info("round %d\t loss: %.3f\t reward: %.2f\t value: %.3f\t pos_reward_ct: %d"
                     % (k, loss, reward, value, pos_reward_ct))
            print("round time %.2f  total time %.2f\n" % (time.time() - tic, time.time() - start))

            if (k + 1) % args.save_every == 0 and args.train:
                print("save models...")
                for model in models:
                    model.save(save_dir, k)
